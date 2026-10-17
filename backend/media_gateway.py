"""FastAPI media gateway and monotonic 1,024-byte PCM frame pacer."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.config import DEFAULT_BIAS_SCORE, settings
from backend.prisma_controller import PrismaController, VADMode

PCM_FRAME_BYTES = 1024
SUPPORTED_SAMPLE_RATES = (8000, 16000)
FrameHandler = Callable[[bytes], Awaitable[None] | None]
TranscriptHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class PCMFramePacer:
    """Dispatch at most one exact PCM frame per monotonic clock tick."""

    def __init__(
        self,
        frame_handler: FrameHandler | None = None,
        *,
        sample_rate: int = 16000,
        prisma_controller: PrismaController | None = None,
        pad_partial_on_flush: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise ValueError("sample_rate must be 8000 or 16000")
        if frame_handler is None and prisma_controller is not None:
            frame_handler = prisma_controller.send_audio_frame
        self.frame_handler = frame_handler
        self.sample_rate = sample_rate
        self.interval_seconds = 0.032 if sample_rate == 16000 else 0.064
        self.pad_partial_on_flush = pad_partial_on_flush
        self._clock = clock
        self._sleeper = sleeper
        self._buffer = bytearray()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self.frames_dispatched = 0
        self.dispatched_frames: list[bytes] = []
        self.dispatch_timestamps: list[float] = []

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    @property
    def interval(self) -> float:
        """Compatibility name for the fixed cadence in seconds."""

        return self.interval_seconds

    @property
    def cadence_seconds(self) -> float:
        return self.interval_seconds

    @property
    def running(self) -> bool:
        return self._running

    def ingest_chunk(self, data: bytes | bytearray | memoryview) -> int:
        """Append arbitrary client chunk bytes and return the new buffer size."""

        try:
            if not isinstance(data, (bytes, bytearray, memoryview)):
                raise TypeError("PCM chunk must be bytes-like")
            normalized = bytes(data)
            if not normalized:
                return len(self._buffer)
            self._buffer.extend(normalized)
            return len(self._buffer)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"unable to ingest PCM chunk: {exc}") from exc

    async def _dispatch_one(self) -> bytes | None:
        """Pop and dispatch only one frame, even if the buffer has many."""

        if len(self._buffer) < PCM_FRAME_BYTES:
            return None
        frame = bytes(self._buffer[:PCM_FRAME_BYTES])
        del self._buffer[:PCM_FRAME_BYTES]
        try:
            if self.frame_handler is not None:
                result = self.frame_handler(frame)
                if inspect.isawaitable(result):
                    await result
            self.frames_dispatched += 1
            self.dispatched_frames.append(frame)
            self.dispatch_timestamps.append(self._clock())
            return frame
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"PCM frame dispatch failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

    async def run(self) -> None:
        """Run the fixed cadence until stopped; never catch up by bursting."""

        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        next_tick = self._clock() + self.interval_seconds
        try:
            while not self._stop_event.is_set():
                delay = max(0.0, next_tick - self._clock())
                if delay:
                    await self._sleeper(delay)
                if self._stop_event.is_set():
                    break
                await self._dispatch_one()
                next_tick += self.interval_seconds
                now = self._clock()
                if next_tick <= now:
                    # We missed a tick; advance one interval rather than
                    # dispatching multiple frames in the same event-loop turn.
                    next_tick = now + self.interval_seconds
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"PCM pacer stopped on dispatch error: {exc}") from exc
        finally:
            self._running = False

    async def start(self) -> None:
        """Start one background pacing task."""

        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self.run(), name="pcm-frame-pacer")

    async def flush(self) -> bytes | None:
        """Discard the tail, or pad one incomplete tail to one valid frame."""

        try:
            if not self._buffer:
                return None
            tail = bytes(self._buffer)
            self._buffer.clear()
            if not self.pad_partial_on_flush:
                return None
            padded = tail.ljust(PCM_FRAME_BYTES, b"\x00")
            if len(padded) != PCM_FRAME_BYTES:
                raise RuntimeError("padded PCM tail violated the frame contract")
            if self.frame_handler is not None:
                result = self.frame_handler(padded)
                if inspect.isawaitable(result):
                    await result
            self.frames_dispatched += 1
            self.dispatched_frames.append(padded)
            self.dispatch_timestamps.append(self._clock())
            return padded
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"PCM tail flush failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

    async def stop(self, *, flush: bool = False) -> None:
        """Stop the background task, optionally padding one tail frame."""

        self._stop_event.set()
        task = self._task
        self._task = None
        current = asyncio.current_task()
        try:
            if task is not None and task is not current and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if flush:
                await self.flush()
            else:
                self._buffer.clear()
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"PCM pacer teardown failed: {exc}") from exc

    async def close(self, *, flush: bool = False) -> None:
        """Teardown alias for call-session cleanup."""

        await self.stop(flush=flush)


class GatewayControlMessage(BaseModel):
    """Pydantic validation for every JSON control message from a client."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["set_vad_mode", "update_bias"]
    mode: VADMode | None = None
    words: list[str] | None = None
    bias_score: float = Field(default=DEFAULT_BIAS_SCORE, gt=0, le=10)

    @field_validator("words")
    @classmethod
    def validate_words(cls, words: list[str] | None) -> list[str] | None:
        if words is None:
            return None
        if not words or any(not isinstance(word, str) or not word.strip() for word in words):
            raise ValueError("update_bias words must be non-empty strings")
        return [word.strip() for word in words]

    @model_validator(mode="after")
    def validate_event_payload(self) -> GatewayControlMessage:
        if self.event == "set_vad_mode" and self.mode is None:
            raise ValueError("set_vad_mode requires mode")
        if self.event == "update_bias" and self.words is None:
            raise ValueError("update_bias requires words")
        if self.event == "set_vad_mode" and self.words is not None:
            raise ValueError("set_vad_mode does not accept words")
        return self


class MediaGateway:
    """Own one controller/pacer pair per WebSocket call."""

    def __init__(self) -> None:
        self._transcript_handler: TranscriptHandler | None = None

    def set_transcript_handler(self, handler: TranscriptHandler | None) -> None:
        """Register the Phase 3 transcript hook without importing Phase 3."""

        self._transcript_handler = handler

    async def _invoke_transcript_hook(self, payload: dict[str, Any]) -> None:
        handler = self._transcript_handler
        if handler is None:
            return
        try:
            result = handler(payload)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(f"transcript hook failed: {exc}") from exc

    async def handle_call(
        self,
        websocket: WebSocket,
        *,
        sample_rate: int = 16000,
        vad_mode: str = "FAST_TURN",
    ) -> None:
        """Run a validated bidirectional call session."""

        if sample_rate not in SUPPORTED_SAMPLE_RATES:
            await websocket.close(code=1008, reason="sample_rate must be 8000 or 16000")
            return
        if vad_mode not in ("FAST_TURN", "DIGIT_CAPTURE"):
            await websocket.close(code=1008, reason="invalid vad_mode")
            return

        await websocket.accept()
        send_lock = asyncio.Lock()
        closed = False

        async def send_json(payload: dict[str, Any]) -> None:
            nonlocal closed
            if closed:
                return
            try:
                async with send_lock:
                    await websocket.send_json(payload)
            except (WebSocketDisconnect, RuntimeError, OSError):
                closed = True

        controller: PrismaController | None = None

        async def on_barge_in(latency_ms: float) -> None:
            await send_json(
                {
                    "event": "clear_audio",
                    "latency_ms": round(latency_ms, 3),
                }
            )

        async def on_transcript(payload: dict[str, Any]) -> None:
            outbound = {**payload, "event": "transcript"}
            await send_json(outbound)
            if controller is not None:
                await send_json(
                    {
                        "event": "telemetry",
                        "sample_rate": sample_rate,
                        "pacer_interval_ms": controller_interval,
                        **controller.telemetry_state(),
                    }
                )
            await self._invoke_transcript_hook(payload)

        controller_interval = 32 if sample_rate == 16000 else 64
        controller = PrismaController(
            on_barge_in=on_barge_in,
            on_transcript=on_transcript,
        )
        pacer = PCMFramePacer(
            prisma_controller=controller,
            sample_rate=sample_rate,
        )
        try:
            await controller.set_vad_mode(vad_mode)  # type: ignore[arg-type]
            await controller.start()
            await pacer.start()
            while not closed:
                message = await websocket.receive()
                message_type = message.get("type")
                if message_type == "websocket.disconnect":
                    break
                if message_type != "websocket.receive":
                    continue
                binary_data = message.get("bytes")
                if binary_data is not None:
                    pacer.ingest_chunk(binary_data)
                    continue
                text_data = message.get("text")
                if text_data is None:
                    continue
                try:
                    control = GatewayControlMessage.model_validate_json(text_data)
                except (ValidationError, ValueError) as exc:
                    await send_json(
                        {
                            "event": "error",
                            "error": "invalid control message",
                            "detail": str(exc),
                        }
                    )
                    continue
                if control.event == "set_vad_mode":
                    state = await controller.set_vad_mode(control.mode)  # type: ignore[arg-type]
                    await send_json({"event": "telemetry", **state})
                elif control.event == "update_bias":
                    update = await controller.update_bias_settings(
                        control.words or [], control.bias_score
                    )
                    await send_json(
                        {
                            "event": "telemetry",
                            "bias_update": update,
                            **controller.telemetry_state(),
                        }
                    )
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            if not closed:
                await send_json({"event": "error", "error": str(exc)})
        finally:
            closed = True
            await pacer.stop(flush=False)
            await controller.close()


app = FastAPI(title="Artha-Suraksha Media Gateway")
gateway = MediaGateway()


def set_transcript_handler(handler: TranscriptHandler | None) -> None:
    """Register the application-wide Phase 3 transcript callback."""

    gateway.set_transcript_handler(handler)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Expose transport mode and synthetic database readiness."""

    return {
        "status": "ok",
        "active_mode": "mock" if settings.USE_MOCK_GNANI else "live",
        "active_key_profile": settings.ACTIVE_KEY_PROFILE,
        "database_status": "synthetic-local-ready",
    }


@app.websocket("/ws/call")
async def call_websocket(
    websocket: WebSocket,
    sample_rate: int = 16000,
    vad_mode: str = "FAST_TURN",
) -> None:
    """Stream binary PCM and JSON acoustic controls for one call."""

    await gateway.handle_call(
        websocket,
        sample_rate=sample_rate,
        vad_mode=vad_mode,
    )
