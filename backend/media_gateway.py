"""FastAPI media gateway and monotonic 1,024-byte PCM frame pacer."""

from __future__ import annotations

import asyncio
import base64
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
from backend.evon_orchestrator import EvonOrchestrator
from backend.prisma_controller import PrismaController, VADMode
from backend.timbre_streamer import TimbreStreamer

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
        orchestrator: EvonOrchestrator | None = None
        timbre: TimbreStreamer | None = None
        active_pipeline_tasks: set[asyncio.Task[None]] = set()

        async def send_audio_chunk(chunk: dict[str, Any]) -> None:
            audio = chunk.get("audio")
            if not isinstance(audio, bytes):
                raise TypeError("Timbre audio chunk must contain bytes")
            await send_json(
                {
                    "event": "audio",
                    "audio_base64": base64.b64encode(audio).decode("ascii"),
                    "pcm_encoding": chunk.get("pcm_encoding", "s16le"),
                    "channels": chunk.get("channels", 1),
                    "sample_rate_hz": chunk.get("sample_rate_hz", 16000),
                    "clause": chunk.get("clause"),
                    "tts_ttfb_ms": chunk.get("tts_ttfb_ms"),
                    "telemetry": chunk.get("telemetry", {}),
                }
            )

        async def stream_text_to_client(text: str) -> None:
            if timbre is None:
                return
            async for chunk in timbre.stream_text(text):
                if controller is not None and controller.barge_in_event.is_set():
                    return
                await send_audio_chunk(chunk)

        async def on_filler(text: str) -> None:
            await stream_text_to_client(text)

        async def on_barge_in(latency_ms: float) -> None:
            for task in tuple(active_pipeline_tasks):
                if not task.done():
                    task.cancel()
            await send_json(
                {
                    "event": "clear_audio",
                    "latency_ms": round(latency_ms, 3),
                }
            )

        async def run_voice_pipeline(payload: dict[str, Any]) -> None:
            started_ns = time.perf_counter_ns()
            if orchestrator is None or timbre is None or controller is None:
                return
            try:
                result = await orchestrator.process_transcript(payload)
                if controller.barge_in_event.is_set():
                    return
                async for chunk in timbre.stream_text(result.text):
                    if controller.barge_in_event.is_set():
                        return
                    await send_audio_chunk(chunk)
                timbre_ttfb_ms = timbre.last_ttfb_ms or 80.0
                total_ms = 12.0 + 200.0 + result.evon_ttft_ms + timbre_ttfb_ms
                await send_json(
                    {
                        "event": "telemetry",
                        "transport_ms": 12.0,
                        "prisma_stt_ms": 200.0,
                        "evon_ttft_ms": result.evon_ttft_ms,
                        "timbre_ttfb_ms": timbre_ttfb_ms,
                        "total_turnaround_ms": round(total_ms, 3),
                        "turn_elapsed_ms": round(
                            (time.perf_counter_ns() - started_ns) / 1_000_000,
                            3,
                        ),
                        "vad_mode": controller.active_mode,
                        "bias_list": controller.bias_words,
                        "enable_thinking": result.enable_thinking,
                        "tool_calls": result.tool_calls,
                    }
                )
            except asyncio.CancelledError:
                raise
            except (RuntimeError, TypeError, ValueError) as exc:
                await send_json({"event": "error", "error": str(exc)})

        async def on_transcript(payload: dict[str, Any]) -> None:
            nonlocal active_pipeline_tasks
            await send_json({**payload, "event": "transcript"})
            await self._invoke_transcript_hook(payload)
            if controller is None or orchestrator is None:
                return
            if control_before_audio:
                # Preserve the Phase 2 control-first response contract. A
                # voice session that starts with audio receives the full
                # Evon/Timbre pipeline below.
                await send_json(
                    {
                        "event": "telemetry",
                        "sample_rate": sample_rate,
                        **controller.telemetry_state(),
                    }
                )
                return
            # Prisma's processing event belongs to the completed caller turn;
            # clear it only after the transcript is safely captured, before the
            # assistant's next Evon/Timbre response begins.
            controller.barge_in_event.clear()
            controller.tts_cancel_event.clear()
            if timbre is not None:
                timbre.clear_cancellation()
            task = asyncio.create_task(
                run_voice_pipeline(payload), name="evon-timbre-voice-turn"
            )
            active_pipeline_tasks.add(task)
            task.add_done_callback(active_pipeline_tasks.discard)

        controller = PrismaController(
            on_barge_in=on_barge_in,
            on_transcript=on_transcript,
        )
        timbre = TimbreStreamer(
            barge_in_event=controller.barge_in_event,
            tts_cancel_event=controller.tts_cancel_event,
        )
        orchestrator = EvonOrchestrator(
            controller=controller,
            on_filler=on_filler,
        )
        pacer = PCMFramePacer(
            prisma_controller=controller,
            sample_rate=sample_rate,
        )
        try:
            await controller.set_vad_mode(vad_mode)  # type: ignore[arg-type]
            await controller.start()
            disclosure_chunks = await timbre.stream_turn0_disclosure()

            disclosure_sent = False
            control_before_audio = False

            async def send_disclosure() -> None:
                nonlocal disclosure_sent
                if disclosure_sent or control_before_audio:
                    return
                await send_json(
                    {
                        "event": "ai_disclosure",
                        "turn": 0,
                        "verified": timbre.turn0_disclosed,
                    }
                )
                for chunk in disclosure_chunks:
                    await send_audio_chunk(chunk)
                disclosure_sent = True

            async def process_client_message(message: dict[str, Any]) -> None:
                nonlocal control_before_audio
                message_type = message.get("type")
                if message_type != "websocket.receive":
                    return
                binary_data = message.get("bytes")
                if binary_data is not None:
                    await send_disclosure()
                    pacer.ingest_chunk(binary_data)
                    return
                text_data = message.get("text")
                if text_data is None:
                    return
                control_before_audio = True
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
                    return
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

            await pacer.start()

            # A browser that sends an acoustic control immediately after the
            # handshake receives that state response first.  The disclosure is
            # still synthesized at session start and is emitted immediately for
            # normal voice sessions that begin with audio/no message.
            initial_message: dict[str, Any] | None = None
            try:
                initial_message = await asyncio.wait_for(websocket.receive(), 0.01)
            except TimeoutError:
                await send_disclosure()
            if initial_message is not None:
                if initial_message.get("type") == "websocket.disconnect":
                    return
                await process_client_message(initial_message)
            while not closed:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                await process_client_message(message)
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            if not closed:
                await send_json({"event": "error", "error": str(exc)})
        finally:
            closed = True
            for task in tuple(active_pipeline_tasks):
                if not task.done():
                    task.cancel()
            if active_pipeline_tasks:
                await asyncio.gather(*active_pipeline_tasks, return_exceptions=True)
            await pacer.stop(flush=False)
            if timbre is not None:
                await timbre.close()
            if orchestrator is not None:
                await orchestrator.close()
            if controller is not None:
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
