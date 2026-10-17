"""Raw Prisma v2.5 streaming controller for the Phase 2 transport layer.

The controller keeps the live WebSocket implementation deliberately small and
places all Phase 1 replay behavior behind the same send/receive contract.  In
mock mode it never constructs a network client.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import re
import ssl
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import aiohttp
import websockets
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.config import (
    DEFAULT_BIAS_LIST,
    DEFAULT_BIAS_SCORE,
    DIGIT_CAPTURE_VAD,
    FAST_TURN_VAD,
    settings,
)
from backend.mock_replay import (
    DEFAULT_BATCH_LANGUAGES,
    PCM_FRAME_BYTES,
    MockBatchSttJob,
    MockPrismaStream,
)

VADMode = Literal["FAST_TURN", "DIGIT_CAPTURE"]
BargeInHandler = Callable[[float], Awaitable[None] | None]
TranscriptHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class BiasSettings(BaseModel):
    """Validated shape for Prisma's mid-session lexicon update."""

    model_config = ConfigDict(extra="forbid")

    words: list[str] = Field(min_length=1)
    score: float = Field(default=DEFAULT_BIAS_SCORE, gt=0, le=10)

    @field_validator("words")
    @classmethod
    def normalize_words(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for word in value:
            if not isinstance(word, str) or not word.strip():
                raise ValueError("bias words must be non-empty strings")
            cleaned = word.strip()
            if cleaned not in normalized:
                normalized.append(cleaned)
        if not normalized:
            raise ValueError("at least one bias word is required")
        return normalized


class PrismaInboundEvent(BaseModel):
    """Minimal validation envelope for live or replayed Prisma JSON."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)


class BatchJobRequest(BaseModel):
    """Validated request envelope for the Prisma batch lifecycle."""

    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1)
    audio_bytes: bytes = Field(min_length=1)
    with_denoise: bool = True
    languages: list[str] = Field(default_factory=lambda: list(DEFAULT_BATCH_LANGUAGES))

    @field_validator("with_denoise")
    @classmethod
    def require_denoise(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("with_denoise=True is mandatory for Phase 5")
        return value

    @field_validator("languages")
    @classmethod
    def require_three_languages(cls, value: list[str]) -> list[str]:
        cleaned = [
            language.strip()
            for language in value
            if isinstance(language, str) and language.strip()
        ]
        if len(cleaned) != 3:
            raise ValueError("exactly three fallback languages are required")
        return cleaned


class PrismaController:
    """Manage primary/secondary Prisma channels and acoustic state."""

    def __init__(
        self,
        *,
        on_barge_in: BargeInHandler | None = None,
        on_transcript: TranscriptHandler | None = None,
        mock_stream: MockPrismaStream | None = None,
    ) -> None:
        self.on_barge_in = on_barge_in
        self.on_transcript = on_transcript
        self._provided_mock_stream = mock_stream
        self._channels: dict[str, Any] = {}
        self._receiver_tasks: dict[str, asyncio.Task[None]] = {}
        self._active_mode: VADMode = "FAST_TURN"
        self._connected = False
        self._closed = False
        self._mock_turn_started: dict[str, bool] = {
            "FAST_TURN": False,
            "DIGIT_CAPTURE": False,
        }
        self._vad_presets: dict[str, dict[str, str]] = {
            "FAST_TURN": dict(FAST_TURN_VAD),
            "DIGIT_CAPTURE": dict(DIGIT_CAPTURE_VAD),
        }
        self._bias_words = list(DEFAULT_BIAS_LIST)
        self._bias_score = DEFAULT_BIAS_SCORE
        self.control_frames: list[dict[str, Any]] = []
        self.last_barge_in_latency_ms: float | None = None
        self.barge_in_event = asyncio.Event()
        self.tts_cancel_event = asyncio.Event()
        self._digit_segments: list[str] = []
        self._digit_transcript = ""
        self._batch_jobs: dict[str, MockBatchSttJob | dict[str, Any]] = {}

    @property
    def active_mode(self) -> VADMode:
        return self._active_mode

    @property
    def active_socket(self) -> Any | None:
        return self._channels.get(self._active_mode)

    @property
    def mock_stream(self) -> MockPrismaStream | None:
        """Return the active replay channel for test and local integration use."""

        stream = self.active_socket
        return stream if isinstance(stream, MockPrismaStream) else None

    @property
    def digit_transcript(self) -> str:
        return self._digit_transcript

    @property
    def active_vad_headers(self) -> dict[str, str]:
        return dict(self._vad_presets[self._active_mode])

    @property
    def vad_presets(self) -> dict[str, dict[str, str]]:
        """Expose immutable-by-copy VAD state to telemetry and tests."""

        return {mode: dict(headers) for mode, headers in self._vad_presets.items()}

    @property
    def bias_words(self) -> list[str]:
        return list(self._bias_words)

    @property
    def bias_score(self) -> float:
        return self._bias_score

    def vad_headers(self, mode: VADMode) -> dict[str, str]:
        """Build backend-only live handshake headers for a VAD mode."""

        self._validate_mode(mode)
        try:
            headers = dict(self._vad_presets[mode])
            # The upstream OQ-3 header is isolated in FAST_TURN_VAD during
            # Phase 1. Live handshakes carry the same configured threshold for
            # both pre-warmed sockets without mutating either preset.
            headers.setdefault(
                "x-speech-confidence",
                FAST_TURN_VAD.get("x-speech-confidence", "0.75"),
            )
            return {
                "X-API-Key-ID": settings.get_active_api_key(),
                **headers,
            }
        except (RuntimeError, KeyError) as exc:
            raise RuntimeError(f"unable to build Prisma headers: {exc}") from exc

    def _validate_mode(self, mode: str) -> None:
        if mode not in ("FAST_TURN", "DIGIT_CAPTURE"):
            raise ValueError("vad mode must be FAST_TURN or DIGIT_CAPTURE")

    @staticmethod
    def _tls13_context() -> ssl.SSLContext:
        try:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.maximum_version = ssl.TLSVersion.TLSv1_3
            return context
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"unable to configure TLS 1.3: {exc}") from exc

    async def connect(self) -> None:
        """Pre-warm both logical VAD channels."""

        if self._connected:
            return
        self._closed = False
        try:
            if settings.USE_MOCK_GNANI:
                self._channels = {
                    "FAST_TURN": self._provided_mock_stream or MockPrismaStream(),
                    "DIGIT_CAPTURE": MockPrismaStream(),
                }
            else:
                context = self._tls13_context()
                for mode in ("FAST_TURN", "DIGIT_CAPTURE"):
                    self._channels[mode] = await websockets.connect(
                        settings.GNANI_STT_WSS_URL,
                        extra_headers=self.vad_headers(mode),
                        ssl=context,
                    )
            self._connected = True
        except (OSError, RuntimeError, websockets.WebSocketException) as exc:
            await self.close()
            raise RuntimeError(f"unable to connect Prisma channels: {exc}") from exc
        except asyncio.CancelledError:
            await self.close()
            raise

    async def start(self) -> None:
        """Connect and start background receive loops for both channels."""

        await self.connect()
        try:
            for mode in ("FAST_TURN", "DIGIT_CAPTURE"):
                task = self._receiver_tasks.get(mode)
                if task is None or task.done():
                    self._receiver_tasks[mode] = asyncio.create_task(
                        self._background_receive_loop(mode),
                        name=f"prisma-receive-{mode.lower()}",
                    )
        except (RuntimeError, asyncio.CancelledError):
            await self.close()
            raise

    async def _send_live(self, channel: Any, payload: bytes | str) -> None:
        try:
            await channel.send(payload)
        except (OSError, websockets.WebSocketException) as exc:
            raise RuntimeError(f"Prisma WebSocket send failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

    async def send_audio_frame(self, frame: bytes) -> None:
        """Send exactly one 1,024-byte PCM frame on the active channel."""

        if not isinstance(frame, bytes):
            raise TypeError("Prisma audio frame must be bytes")
        if len(frame) != PCM_FRAME_BYTES:
            raise ValueError(
                f"Prisma audio frame must be exactly {PCM_FRAME_BYTES} bytes; "
                f"got {len(frame)}"
            )
        if not self._connected:
            await self.connect()
        if self._closed:
            raise RuntimeError("Prisma controller is closed")
        try:
            channel = self.active_socket
            if channel is None:
                raise RuntimeError("active Prisma channel is not connected")
            if isinstance(channel, MockPrismaStream):
                turn_started = self._mock_turn_started[self._active_mode]
                await channel.send_frame(frame, emit_processing=not turn_started)
                if not turn_started:
                    self._mock_turn_started[self._active_mode] = True
                    await channel.simulate_speech()
            else:
                await self._send_live(channel, frame)
        except (OSError, RuntimeError, websockets.WebSocketException) as exc:
            raise RuntimeError(f"unable to send Prisma audio frame: {exc}") from exc
        except asyncio.CancelledError:
            raise

    async def update_bias_settings(
        self, bias_words: list[str], bias_score: float = DEFAULT_BIAS_SCORE
    ) -> dict[str, Any]:
        """Send a validated mid-call ``update_settings`` control frame."""

        try:
            update = BiasSettings(words=bias_words, score=bias_score)
            payload = {
                "type": "update_settings",
                "x-bias-list": update.words,
                "x-bias-score": update.score,
            }
            if not self._connected:
                await self.connect()
            channel = self.active_socket
            if channel is None:
                raise RuntimeError("active Prisma channel is not connected")
            self._bias_words = list(update.words)
            self._bias_score = update.score
            self.control_frames.append(payload)
            if not isinstance(channel, MockPrismaStream):
                await self._send_live(channel, json.dumps(payload, ensure_ascii=False))
            return dict(payload)
        except (TypeError, ValueError, RuntimeError, websockets.WebSocketException) as exc:
            raise RuntimeError(f"unable to update Prisma bias settings: {exc}") from exc
        except asyncio.CancelledError:
            raise

    async def set_vad_mode(self, mode: VADMode) -> dict[str, Any]:
        """Switch the active outbound channel without clearing its frame queue."""

        try:
            self._validate_mode(mode)
            if not self._connected:
                await self.connect()
            if self._channels.get(mode) is None:
                raise RuntimeError(f"Prisma channel is unavailable: {mode}")
            self._active_mode = mode
            return self.telemetry_state()
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"unable to switch Prisma VAD mode: {exc}") from exc
        except asyncio.CancelledError:
            raise

    def accumulate_digit_transcript(self, segment: str) -> str:
        """Merge transcript pieces such as ``A/C XX45`` and ``21``."""

        if not isinstance(segment, str) or not segment.strip():
            raise ValueError("digit transcript segment must be non-empty text")
        try:
            self._digit_segments.append(segment.strip())
            joined = " ".join(self._digit_segments)
            account_match = re.search(
                r"A\s*/\s*C\s+(.+)$", joined, flags=re.IGNORECASE
            )
            if account_match:
                tokens = re.findall(r"[A-Za-z0-9]+", account_match.group(1))
                if tokens:
                    suffix = tokens[0].upper()
                    for token in tokens[1:]:
                        if token.isdigit():
                            suffix += token
                        else:
                            break
                    self._digit_transcript = f"A/C {suffix}"
                else:
                    self._digit_transcript = "A/C"
            else:
                self._digit_transcript = joined
            return self._digit_transcript
        except (TypeError, ValueError, re.error) as exc:
            raise RuntimeError(f"unable to merge digit transcript: {exc}") from exc

    def merge_digit_segments(self, segments: list[str]) -> str:
        """Reset and merge a complete list of split account-number segments."""

        if not isinstance(segments, list):
            raise TypeError("digit segments must be a list")
        self._digit_segments = []
        self._digit_transcript = ""
        for segment in segments:
            self.accumulate_digit_transcript(segment)
        return self._digit_transcript

    async def simulate_mock_speech(
        self, query: str | None = None, *, mode: VADMode | None = None
    ) -> None:
        """Queue a deterministic replay turn for a test or local console."""

        try:
            if not settings.USE_MOCK_GNANI:
                raise RuntimeError("mock speech is available only in mock mode")
            if not self._connected:
                await self.connect()
            selected_mode = mode or self._active_mode
            self._validate_mode(selected_mode)
            stream = self._channels.get(selected_mode)
            if not isinstance(stream, MockPrismaStream):
                raise TypeError("selected channel is not a mock stream")
            await stream.simulate_speech(query)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"unable to simulate Prisma speech: {exc}") from exc
        except asyncio.CancelledError:
            raise

    async def _invoke_callback(
        self,
        callback: Callable[..., Awaitable[None] | None] | None,
        *args: Any,
    ) -> None:
        if callback is None:
            return
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Prisma callback failed: {exc}") from exc

    @staticmethod
    def _decode_event(raw_event: Any) -> dict[str, Any]:
        try:
            if isinstance(raw_event, bytes):
                raw_event = raw_event.decode("utf-8")
            if isinstance(raw_event, str):
                raw_event = json.loads(raw_event)
            if not isinstance(raw_event, dict):
                raise TypeError("Prisma event must be a JSON object")
            return PrismaInboundEvent.model_validate(raw_event).model_dump(
                mode="python"
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid Prisma event: {exc}") from exc

    async def _handle_event(self, raw_event: Any, mode: str) -> dict[str, Any]:
        """Handle one event, prioritizing processing/barge-in dispatch."""

        received_ns = time.perf_counter_ns()
        event = self._decode_event(raw_event)
        event_type = event.get("type")
        if event_type == "processing":
            self.tts_cancel_event.set()
            self.barge_in_event.set()
            dispatch_latency_ms = (time.perf_counter_ns() - received_ns) / 1_000_000
            self.last_barge_in_latency_ms = dispatch_latency_ms
            await self._invoke_callback(self.on_barge_in, dispatch_latency_ms)
            self.last_barge_in_latency_ms = (
                time.perf_counter_ns() - received_ns
            ) / 1_000_000
        is_final = bool(event.get("is_final")) or event.get("status") == "completed"
        if event_type in {"transcript", "final", "completed"} and is_final:
            text = event.get("text") or event.get("transcript")
            if self._active_mode == "DIGIT_CAPTURE" and isinstance(text, str):
                event["digit_transcript"] = self.accumulate_digit_transcript(text)
            await self._invoke_callback(self.on_transcript, event)
        return event

    async def _receive_one_live(self, channel: Any) -> Any:
        try:
            return await channel.recv()
        except (OSError, websockets.WebSocketException) as exc:
            raise RuntimeError(f"Prisma WebSocket receive failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

    async def _background_receive_loop(self, mode: str) -> None:
        """Keep a channel receiver alive for the duration of a call."""

        try:
            channel = self._channels[mode]
            while not self._closed:
                if isinstance(channel, MockPrismaStream):
                    try:
                        raw_event = await channel.__anext__()
                    except StopAsyncIteration:
                        await asyncio.sleep(0.001)
                        continue
                else:
                    raw_event = await self._receive_one_live(channel)
                await self._handle_event(raw_event, mode)
        except asyncio.CancelledError:
            raise
        except (KeyError, RuntimeError, OSError, websockets.WebSocketException):
            if not self._closed:
                raise

    async def receive_loop(
        self, mode: VADMode | None = None, *, keep_alive: bool = False
    ) -> list[dict[str, Any]]:
        """Drain a channel for direct use, or run it continuously when requested."""

        selected_mode = mode or self._active_mode
        self._validate_mode(selected_mode)
        if not self._connected:
            await self.connect()
        if keep_alive:
            await self._background_receive_loop(selected_mode)
            return []
        channel = self._channels[selected_mode]
        events: list[dict[str, Any]] = []
        try:
            if isinstance(channel, MockPrismaStream):
                while True:
                    try:
                        raw_event = await channel.__anext__()
                    except StopAsyncIteration:
                        break
                    events.append(await self._handle_event(raw_event, selected_mode))
            else:
                raw_event = await self._receive_one_live(channel)
                events.append(await self._handle_event(raw_event, selected_mode))
            return events
        except (RuntimeError, OSError, websockets.WebSocketException) as exc:
            raise RuntimeError(f"Prisma receive loop failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

    def telemetry_state(self) -> dict[str, Any]:
        """Return JSON-safe acoustic state for the gateway inspector."""

        headers = self.active_vad_headers
        return {
            "active_vad_mode": self._active_mode,
            "vad_mode": self._active_mode,
            "active_vad_headers": headers,
            "x-min-speech-ms": headers.get("x-min-speech-ms"),
            "x-min-silence-ms": headers.get("x-min-silence-ms"),
            "x-min-segment-ms": headers.get("x-min-segment-ms"),
            "active_bias_list": list(self._bias_words),
            "bias_words": list(self._bias_words),
            "active_bias_score": self._bias_score,
            "bias_score": self._bias_score,
            "digit_transcript": self._digit_transcript,
            "barge_in_latency_ms": self.last_barge_in_latency_ms,
            "connected": self._connected and not self._closed,
        }

    @staticmethod
    def _batch_tls13_context() -> ssl.SSLContext:
        try:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.maximum_version = ssl.TLSVersion.TLSv1_3
            return context
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"unable to configure batch TLS 1.3: {exc}") from exc

    async def _batch_live_request(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make one live batch request with an isolated backend key header."""

        try:
            headers = {"X-API-Key-ID": settings.get_active_api_key()}
            timeout = aiohttp.ClientTimeout(total=30.0)
            connector = aiohttp.TCPConnector(ssl=self._batch_tls13_context())
            async with (
                aiohttp.ClientSession(
                    connector=connector, timeout=timeout, headers=headers
                ) as session,
                session.request(method, url, json=payload) as response,
            ):
                response.raise_for_status()
                result = await response.json()
            if not isinstance(result, dict):
                raise TypeError("batch response must be a JSON object")
            return result
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"Prisma batch request failed: {exc}") from exc

    async def create_batch_job(
        self,
        file_name: str,
        audio_bytes: bytes,
        with_denoise: bool = True,
        languages: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a denoised, three-language Prisma batch job."""

        try:
            request = BatchJobRequest(
                file_name=file_name,
                audio_bytes=audio_bytes,
                with_denoise=with_denoise,
                languages=(
                    list(DEFAULT_BATCH_LANGUAGES)
                    if languages is None
                    else languages
                ),
            )
            if settings.USE_MOCK_GNANI:
                job = MockBatchSttJob(
                    request.file_name,
                    request.audio_bytes,
                    with_denoise=request.with_denoise,
                    languages=request.languages,
                )
                self._batch_jobs[job.job_id] = job
                return {
                    "job_id": job.job_id,
                    "status": "CREATED",
                    "history": ["CREATED"],
                    "with_denoise": True,
                    "languages": request.languages,
                    "nominal_poll_interval_s": 10.0,
                }
            payload = {
                "file_name": request.file_name,
                "audio_base64": base64.b64encode(request.audio_bytes).decode("ascii"),
                "with_denoise": True,
                "languages": request.languages,
            }
            result = await self._batch_live_request(
                "POST", settings.GNANI_BATCH_STT_URL, payload=payload
            )
            job_id = result.get("job_id") or result.get("id")
            if not isinstance(job_id, str) or not job_id:
                raise ValueError("live batch response did not provide job_id")
            result.setdefault("job_id", job_id)
            result.setdefault("history", ["CREATED"])
            result.setdefault("with_denoise", True)
            result.setdefault("languages", request.languages)
            result.setdefault("nominal_poll_interval_s", 10.0)
            self._batch_jobs[job_id] = result
            return result
        except asyncio.CancelledError:
            raise
        except (ValidationError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"unable to create Prisma batch job: {exc}") from exc

    async def start_batch_job(self, job_id: str) -> dict[str, Any]:
        """Transition a created batch job to STARTED."""

        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id cannot be empty")
        try:
            job = self._batch_jobs.get(job_id)
            if settings.USE_MOCK_GNANI:
                if not isinstance(job, MockBatchSttJob):
                    raise KeyError(f"unknown mock batch job: {job_id}")
                return await job.start()
            if not isinstance(job, dict):
                raise KeyError(f"unknown live batch job: {job_id}")
            result = await self._batch_live_request(
                "POST", f"{settings.GNANI_BATCH_STT_URL}/{job_id}/start"
            )
            result.setdefault("job_id", job_id)
            result.setdefault("status", "STARTED")
            result.setdefault("history", ["CREATED", "STARTED"])
            self._batch_jobs[job_id] = result
            return result
        except asyncio.CancelledError:
            raise
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"unable to start Prisma batch job: {exc}") from exc

    async def poll_batch_job(
        self,
        job_id: str,
        poll_interval_s: float = 10.0,
        max_attempts: int = 30,
    ) -> dict[str, Any]:
        """Poll until a batch transcript completes, preserving nominal cadence metadata."""

        if poll_interval_s < 0 or max_attempts < 1:
            raise ValueError("poll_interval_s must be non-negative and max_attempts >= 1")
        try:
            job = self._batch_jobs.get(job_id)
            if settings.USE_MOCK_GNANI:
                if not isinstance(job, MockBatchSttJob):
                    raise KeyError(f"unknown mock batch job: {job_id}")
                if job.status == "CREATED":
                    await job.start()
                for _ in range(max_attempts):
                    result = await job.poll(poll_interval_s)
                    if result.get("status") == "COMPLETED":
                        return result
                raise TimeoutError(f"batch job did not complete: {job_id}")

            if not isinstance(job, dict):
                raise KeyError(f"unknown live batch job: {job_id}")
            prior_history = list(job.get("history", ["CREATED", "STARTED"]))
            for _ in range(max_attempts):
                result = await self._batch_live_request(
                    "GET", f"{settings.GNANI_BATCH_STT_URL}/{job_id}"
                )
                status = str(result.get("status", "")).upper()
                response_history = result.get("history")
                history = (
                    list(response_history)
                    if isinstance(response_history, list)
                    else list(prior_history)
                )
                for milestone in ("CREATED", "STARTED"):
                    if milestone not in history:
                        history.insert(0 if milestone == "CREATED" else len(history), milestone)
                if "POLLING" not in history:
                    history.append("POLLING")
                result["history"] = history
                result.setdefault("job_id", job_id)
                self._batch_jobs[job_id] = result
                if status in {"COMPLETED", "FAILED", "ERROR"}:
                    if status != "COMPLETED":
                        raise RuntimeError(f"batch job ended in {status}: {job_id}")
                    if "COMPLETED" not in history:
                        history.append("COMPLETED")
                    return result
                await asyncio.sleep(poll_interval_s)
            raise TimeoutError(f"batch job polling timed out: {job_id}")
        except asyncio.CancelledError:
            raise
        except (KeyError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            raise RuntimeError(f"unable to poll Prisma batch job: {exc}") from exc

    async def run_batch_stt_job(
        self,
        file_name: str,
        audio_bytes: bytes,
        *,
        with_denoise: bool = True,
        languages: list[str] | None = None,
        poll_interval_s: float = 10.0,
        max_attempts: int = 30,
    ) -> dict[str, Any]:
        """Run create → start → poll and return a completed denoised transcript."""

        try:
            created = await self.create_batch_job(
                file_name, audio_bytes, with_denoise, languages
            )
            job_id = str(created["job_id"])
            started = await self.start_batch_job(job_id)
            completed = await self.poll_batch_job(
                job_id, poll_interval_s=poll_interval_s, max_attempts=max_attempts
            )
            history = list(completed.get("history", started.get("history", [])))
            return {
                **completed,
                "job_id": job_id,
                "history": history,
                "with_denoise": True,
                "languages": list(
                    completed.get("languages", created.get("languages", []))
                ),
                "poll_interval_s": poll_interval_s,
                "nominal_poll_interval_s": 10.0,
            }
        except asyncio.CancelledError:
            raise
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"batch STT lifecycle failed: {exc}") from exc

    async def close(self) -> None:
        """Cancel receivers and close both live channels without leaking tasks."""

        if self._closed and not self._receiver_tasks:
            return
        self._closed = True
        current = asyncio.current_task()
        tasks = list(self._receiver_tasks.values())
        self._receiver_tasks.clear()
        try:
            for task in tasks:
                if task is not current and not task.done():
                    task.cancel()
            pending = [task for task in tasks if task is not current]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Prisma receiver teardown failed: {exc}") from exc
        finally:
            for channel in list(self._channels.values()):
                if isinstance(channel, MockPrismaStream):
                    continue
                try:
                    await channel.close()
                except (OSError, websockets.WebSocketException):
                    continue
                except asyncio.CancelledError:
                    raise
                except (RuntimeError, TypeError, ValueError):
                    continue
            self._channels.clear()
            self._connected = False

    async def disconnect(self) -> None:
        """Alias used by gateway teardown code."""

        await self.close()
