"""Zero-credit deterministic Prisma and Timbre replay streams.

These streams deliberately have no network client.  They provide the local
contracts needed by later phases while ``USE_MOCK_GNANI`` protects the live
programme credit budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import struct
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.config import settings

PCM_FRAME_BYTES: Final[int] = 1024
AI_DISCLOSURE_TEXT: Final[str] = (
    "Namaste, main Artha-Suraksha ka AI sahayak hoon. "
    "Main aapki sahayata ke liye bana ek krtrim buddhi seva hoon."
)
DEFAULT_TRANSCRIPTS: Final[tuple[tuple[str, str], ...]] = (
    ("hi-Latn", "Mera A/C XX4521 ka EMI kab due hai?"),
    ("hi-IN", "कृपया A/C XX4521 की EMI की स्थिति बताइए।"),
)
DEFAULT_BATCH_LANGUAGES: Final[tuple[str, str, str]] = ("hi-IN", "en-IN", "kn-IN")
MOCK_BATCH_TRANSCRIPT: Final[str] = (
    "Namaste, main Artha-Suraksha ka AI sahayak hoon. "
    "नमस्ते, मेरा A/C XX4521 का बैलेंस ₹1,24,567.50 है और अगली EMI 05/11/2026 को due है; "
    "interest rate 2.5% p.a. है. I want an EMI deferral and PMFBY claim review "
    "for policy PMFBY-2026-8841; कृपया EMI deferral और PMFBY claim request दर्ज करें।"
)


class MockModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MockTelemetry(MockModel):
    transport_ms: float = Field(ge=0, lt=20)
    stt_ms: float = Field(ge=0)
    evon_ttft_ms: float = Field(ge=0)
    timbre_ttfb_ms: float = Field(ge=0, lt=100)
    total_ms: float = Field(ge=0)


class MockAudioChunk(MockModel):
    type: str = "audio"
    audio: bytes
    pcm_encoding: str = "s16le"
    channels: int = Field(default=1, ge=1, le=1)
    sample_rate_hz: int = Field(default=16000, ge=1)
    chunk_index: int = Field(ge=0)
    telemetry: MockTelemetry


def validate_pcm_frame(frame: bytes | bytearray | memoryview) -> bytes:
    """Validate and normalize the cross-phase 1,024-byte PCM contract."""

    if not isinstance(frame, (bytes, bytearray, memoryview)):
        raise TypeError("PCM frame must be bytes-like")
    normalized = bytes(frame)
    if len(normalized) != PCM_FRAME_BYTES:
        raise ValueError(
            f"PCM frame must be exactly {PCM_FRAME_BYTES} bytes; got {len(normalized)}"
        )
    return normalized


class MockPrismaStream:
    """A local async iterable that mimics the important Prisma events."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        self.enabled = settings.USE_MOCK_GNANI if enabled is None else enabled
        self.frames_received = 0
        self._events: deque[dict[str, Any]] = deque()
        self._turn_index = 0

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError("MockPrismaStream requires USE_MOCK_GNANI=true")

    async def send_frame(
        self,
        frame: bytes | bytearray | memoryview,
        *,
        emit_processing: bool = True,
    ) -> None:
        """Accept one and only one correctly aligned PCM frame."""

        try:
            self._require_enabled()
            validate_pcm_frame(frame)
            self.frames_received += 1
            if emit_processing and not any(
                event.get("type") == "processing" for event in self._events
            ):
                self._events.append(
                    {
                        "type": "processing",
                        "frame_index": self.frames_received,
                    }
                )
        except (TypeError, ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f"mock Prisma frame handling failed: {exc}") from exc

    async def send(self, frame: bytes | bytearray | memoryview) -> None:
        """Compatibility alias for callers that use a stream-like send API."""

        await self.send_frame(frame)

    def _transcript_payload(self, query: str | None) -> dict[str, Any]:
        language, default_text = DEFAULT_TRANSCRIPTS[
            self._turn_index % len(DEFAULT_TRANSCRIPTS)
        ]
        text = query.strip() if query is not None else default_text
        if not text:
            raise ValueError("simulated transcript cannot be empty")
        payload = {
            "type": "transcript",
            "event": "completed",
            "status": "completed",
            "is_final": True,
            "language": language,
            "text": text,
            "transcript": text,
            "confidence": 0.98,
            "account_reference": "A/C XX4521" if "XX4521" in text else None,
            "domain_terms": ["EMI"] if "emi" in text.casefold() else [],
            "turn_index": self._turn_index,
        }
        self._turn_index += 1
        return payload

    async def simulate_speech(self, query: str | None = None) -> None:
        """Queue immediate processing followed by one final transcript."""

        try:
            self._require_enabled()
            if not any(event.get("type") == "processing" for event in self._events):
                self._events.append({"type": "processing"})
            self._events.append(self._transcript_payload(query))
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f"mock Prisma speech simulation failed: {exc}") from exc

    async def stream_transcript(
        self, query: str | None = None
    ) -> Iterable[dict[str, Any]]:
        """Queue and yield a complete local transcript turn."""

        await self.simulate_speech(query)
        async for event in self:
            yield event

    def __aiter__(self) -> MockPrismaStream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            if not self._events:
                raise StopAsyncIteration
            return self._events.popleft()
        except StopAsyncIteration:
            raise
        except Exception as exc:
            raise RuntimeError(f"mock Prisma event read failed: {exc}") from exc


class MockBatchSttJob:
    """Deterministic local replay of the Prisma Batch STT state machine."""

    def __init__(
        self,
        file_name: str,
        audio_bytes: bytes,
        *,
        with_denoise: bool = True,
        languages: list[str] | None = None,
    ) -> None:
        if not file_name.strip():
            raise ValueError("batch file_name cannot be empty")
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise ValueError("batch audio_bytes must be non-empty bytes")
        if with_denoise is not True:
            raise ValueError("Phase 5 requires with_denoise=True")
        configured_languages = (
            list(DEFAULT_BATCH_LANGUAGES) if languages is None else list(languages)
        )
        if len(configured_languages) != 3 or any(
            not isinstance(language, str) or not language.strip()
            for language in configured_languages
        ):
            raise ValueError("exactly three fallback languages are required")
        digest = hashlib.sha256(file_name.encode("utf-8") + audio_bytes).hexdigest()[:12]
        self.job_id = f"mock-batch-{digest}"
        self.file_name = file_name
        self.audio_bytes = audio_bytes
        self.with_denoise = True
        self.languages = list(configured_languages)
        self.status = "CREATED"
        self.history = ["CREATED"]
        self.poll_count = 0

    def _payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "file_name": self.file_name,
            "status": self.status,
            "history": list(self.history),
            "with_denoise": self.with_denoise,
            "languages": list(self.languages),
            "nominal_poll_interval_s": 10.0,
            "poll_count": self.poll_count,
            "audio_bytes": len(self.audio_bytes),
        }

    async def start(self) -> dict[str, Any]:
        if self.status != "CREATED":
            raise RuntimeError(f"cannot start batch job in {self.status}")
        self.status = "STARTED"
        self.history.append("STARTED")
        return self._payload()

    async def poll(self, poll_interval_s: float) -> dict[str, Any]:
        if poll_interval_s < 0:
            raise ValueError("poll_interval_s cannot be negative")
        if self.status not in {"STARTED", "POLLING"}:
            if self.status == "COMPLETED":
                return self.completed_payload()
            raise RuntimeError(f"cannot poll batch job in {self.status}")
        self.status = "POLLING"
        if self.history[-1] != "POLLING":
            self.history.append("POLLING")
        self.poll_count += 1
        await asyncio.sleep(poll_interval_s)
        self.status = "COMPLETED"
        self.history.append("COMPLETED")
        return self.completed_payload()

    def completed_payload(self) -> dict[str, Any]:
        if self.status != "COMPLETED":
            raise RuntimeError("batch job is not completed")
        return {
            **self._payload(),
            "transcript": MOCK_BATCH_TRANSCRIPT,
            "verbatim_transcript": MOCK_BATCH_TRANSCRIPT,
            "detected_languages": list(self.languages),
            "segments": [
                {
                    "language": "hi-IN",
                    "text": "मेरा A/C XX4521 का बैलेंस ₹1,24,567.50 है",
                },
                {
                    "language": "hi-Latn",
                    "text": "next EMI 05/11/2026 ko due hai, interest rate 2.5% p.a. hai",
                },
                {
                    "language": "en-IN",
                    "text": "I want an EMI deferral and PMFBY claim review for policy PMFBY-2026-8841.",
                },
                {"language": "kn-IN", "text": "synthetic fallback language configured; claim request"},
            ],
            "denoised": True,
        }


class MockTimbreStream:
    """A deterministic clause-to-PCM stream with fixed latency telemetry."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        self.enabled = settings.USE_MOCK_GNANI if enabled is None else enabled
        self.clauses_rendered = 0

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RuntimeError("MockTimbreStream requires USE_MOCK_GNANI=true")

    @staticmethod
    def _has_disclosure(text: str) -> bool:
        return bool(
            re.search(r"\bai\b", text, flags=re.IGNORECASE)
            or "कृत्रिम" in text
            or "एआई" in text
        )

    @staticmethod
    def _pcm_for_text(text: str) -> bytes:
        """Produce deterministic synthetic signed 16-bit mono PCM bytes."""

        sample_count = max(512, ((len(text) * 64 + 511) // 512) * 512)
        seed = sum(ord(character) for character in text) % 20000
        try:
            return b"".join(
                struct.pack("<h", ((index * 31 + seed) % 20000) - 10000)
                for index in range(sample_count)
            )
        except (struct.error, OverflowError) as exc:
            raise RuntimeError(f"mock PCM synthesis failed: {exc}") from exc

    @staticmethod
    def _telemetry() -> MockTelemetry:
        return MockTelemetry(
            transport_ms=12.0,
            stt_ms=200.0,
            evon_ttft_ms=302.0,
            timbre_ttfb_ms=80.0,
            # The benchmark reports the agreed nominal waterfall budget
            # (~622 ms); the individual stage values remain deterministic and
            # keep the synthetic TTFB below the 100 ms mock target.
            total_ms=622.0,
        )

    async def stream_clause(
        self,
        text: str,
        *,
        turn_index: int = 1,
    ) -> Iterable[dict[str, Any]]:
        """Yield 1,024-byte PCM chunks and one telemetry snapshot per chunk."""

        try:
            self._require_enabled()
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Timbre text clause cannot be empty")
            if turn_index == 0 and not self._has_disclosure(text):
                raise ValueError("Turn-0 Timbre text must contain the AI disclosure")
            pcm = self._pcm_for_text(text)
            telemetry = self._telemetry()
            for index in range(0, len(pcm), PCM_FRAME_BYTES):
                chunk = MockAudioChunk(
                    audio=pcm[index : index + PCM_FRAME_BYTES],
                    chunk_index=index // PCM_FRAME_BYTES,
                    telemetry=telemetry,
                )
                self.clauses_rendered += 1
                yield chunk.model_dump()
        except (TypeError, ValueError, RuntimeError):
            raise
        except (ValidationError, Exception) as exc:
            raise RuntimeError(f"mock Timbre stream failed: {exc}") from exc

    async def synthesize(
        self,
        text: str,
        *,
        turn_index: int = 1,
    ) -> list[dict[str, Any]]:
        """Collect a clause stream for callers that prefer one awaitable."""

        return [
            chunk
            async for chunk in self.stream_clause(text, turn_index=turn_index)
        ]

    async def stream(self, text: str, *, turn_index: int = 1) -> Iterable[dict[str, Any]]:
        """Compatibility alias for the clause stream."""

        async for chunk in self.stream_clause(text, turn_index=turn_index):
            yield chunk


class MockReplayManager:
    """Persist deterministic JSON and aligned PCM exchanges in a local cache."""

    def __init__(self, cache_dir: str | Path = ".mock_replay_cache") -> None:
        self.cache_dir = Path(cache_dir)

    @staticmethod
    def _safe_name(name: str) -> str:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("replay name must be a simple local filename")
        return name

    def _path(self, name: str, suffix: str) -> Path:
        safe_name = self._safe_name(name)
        return self.cache_dir / f"{safe_name}{suffix}"

    def save_json(self, name: str, payload: Any) -> Path:
        try:
            path = self._path(name, ".json")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            return path
        except (TypeError, ValueError, OSError) as exc:
            raise RuntimeError(f"unable to save JSON replay: {exc}") from exc

    def replay_json(self, name: str) -> Any:
        try:
            path = self._path(name, ".json")
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"unable to replay JSON exchange: {exc}") from exc

    def save_pcm(self, name: str, pcm: bytes | bytearray | memoryview) -> Path:
        try:
            normalized = bytes(pcm)
            if len(normalized) % PCM_FRAME_BYTES != 0:
                raise ValueError("PCM replay must contain complete 1,024-byte frames")
            path = self._path(name, ".pcm")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(normalized)
            return path
        except (TypeError, ValueError, OSError) as exc:
            raise RuntimeError(f"unable to save PCM replay: {exc}") from exc

    def replay_pcm(self, name: str) -> bytes:
        try:
            path = self._path(name, ".pcm")
            pcm = path.read_bytes()
            if len(pcm) % PCM_FRAME_BYTES != 0:
                raise ValueError("stored PCM replay is not frame aligned")
            return pcm
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"unable to replay PCM exchange: {exc}") from exc

    def save_exchange(
        self,
        exchange_id: str,
        json_payload: Any,
        pcm: bytes | bytearray | memoryview | None = None,
    ) -> dict[str, str]:
        try:
            json_path = self.save_json(exchange_id, json_payload)
            result = {"json": str(json_path)}
            if pcm is not None:
                result["pcm"] = str(self.save_pcm(exchange_id, pcm))
            return result
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"unable to save replay exchange: {exc}") from exc

    def replay_exchange(self, exchange_id: str) -> dict[str, Any]:
        try:
            result: dict[str, Any] = {"json": self.replay_json(exchange_id)}
            pcm_path = self._path(exchange_id, ".pcm")
            if pcm_path.exists():
                result["pcm"] = self.replay_pcm(exchange_id)
            return result
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError(f"unable to inspect replay exchange: {exc}") from exc
