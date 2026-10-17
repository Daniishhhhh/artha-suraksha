"""Timbre v2.5/v3.0 clause streaming with disclosure and SSML guardrails."""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import re
import ssl
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, ClassVar

import websockets
from pydantic import BaseModel, ConfigDict, Field

from backend.config import settings
from backend.mock_replay import AI_DISCLOSURE_TEXT, PCM_FRAME_BYTES, MockTimbreStream

CLAUSE_BOUNDARIES = frozenset("।.,?!")
SSML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
PUNCTUATION = "।.,?!"
AudioObserver = Callable[[dict[str, Any]], Awaitable[None] | None]


def strip_ssml(text: str) -> str:
    """Remove XML/SSML tags while preserving text and ``[mood]`` markers."""

    if not isinstance(text, str):
        raise TypeError("Timbre text must be a string")
    return SSML_TAG_RE.sub("", text)


def contains_ssml(text: str) -> bool:
    """Return whether a clause contains an XML-style speech tag."""

    return bool(SSML_TAG_RE.search(text))


class TimbreTelemetry(BaseModel):
    """Validated metrics attached to each synthetic or live audio chunk."""

    model_config = ConfigDict(extra="allow")

    transport_ms: float = Field(default=12.0, ge=0)
    stt_ms: float = Field(default=200.0, ge=0)
    evon_ttft_ms: float = Field(default=302.0, ge=0)
    timbre_ttfb_ms: float = Field(default=80.0, ge=0)
    total_ms: float = Field(default=594.0, ge=0)


class ClauseChunker:
    """Incrementally split text at safe spoken-clause boundaries.

    Commas inside Indian grouped currency, periods in decimals, and the two
    periods in ``p.a.`` are deliberately treated as text rather than sentence
    boundaries.  The chunker includes the boundary in the emitted clause so
    Timbre receives natural punctuation.
    """

    def __init__(self) -> None:
        self._buffer = ""

    @property
    def buffered_text(self) -> str:
        return self._buffer

    @staticmethod
    def _is_numeric_group(text: str, index: int) -> bool:
        return (
            index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        )

    @staticmethod
    def _is_protected_period(text: str, index: int) -> bool:
        if ClauseChunker._is_numeric_group(text, index):
            return True
        left = text[max(0, index - 4) : index + 1].casefold()
        if left.endswith("p."):
            return True
        if left.endswith("p.a."):
            return True
        return index > 0 and text[index - 1].casefold() == "a" and "p.a" in left

    @classmethod
    def _is_boundary(cls, text: str, index: int) -> bool:
        marker = text[index]
        if marker not in CLAUSE_BOUNDARIES:
            return False
        if marker == "," and cls._is_numeric_group(text, index):
            return False
        return not (marker == "." and cls._is_protected_period(text, index))

    def feed(self, token_delta: str) -> list[str]:
        """Append a token delta and return every newly completed clause."""

        if not isinstance(token_delta, str):
            raise TypeError("token delta must be text")
        self._buffer += strip_ssml(token_delta)
        clauses: list[str] = []
        start = 0
        for index, _ in enumerate(self._buffer):
            if not self._is_boundary(self._buffer, index):
                continue
            clause = self._buffer[start : index + 1].strip()
            if clause:
                clauses.append(clause)
            start = index + 1
        if start:
            self._buffer = self._buffer[start:]
        return clauses

    def flush(self) -> list[str]:
        """Flush a final unterminated clause, if any."""

        clause = self._buffer.strip()
        self._buffer = ""
        return [clause] if clause else []

    def chunk(self, text: str) -> list[str]:
        """Convenience method for deterministic, complete text."""

        return [*self.feed(text), *self.flush()]


async def chunk_by_clause(token_stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """Yield punctuation-complete clauses from an async token stream."""

    chunker = ClauseChunker()
    try:
        async for token_delta in token_stream:
            for clause in chunker.feed(token_delta):
                yield clause
        for clause in chunker.flush():
            yield clause
    except asyncio.CancelledError:
        raise
    except (TypeError, ValueError, RuntimeError) as exc:
        raise RuntimeError(f"clause token stream failed: {exc}") from exc


class TimbreStreamer:
    """Turn-aware wrapper around mock and raw Timbre WebSocket streaming."""

    DISCLOSURES: ClassVar[dict[str, str]] = {
        "hi-IN": AI_DISCLOSURE_TEXT,
        "hi-Latn": AI_DISCLOSURE_TEXT,
        "en-IN": (
            "Namaste, you are speaking with the Artha-Suraksha AI Assistant. "
            "This is an AI service powered by Gnani AI."
        ),
    }

    def __init__(
        self,
        *,
        barge_in_event: asyncio.Event | None = None,
        tts_cancel_event: asyncio.Event | None = None,
        mock_stream: MockTimbreStream | None = None,
        model: str = "timbre-v2.5",
        on_audio: AudioObserver | None = None,
    ) -> None:
        self.barge_in_event = barge_in_event or asyncio.Event()
        self.tts_cancel_event = tts_cancel_event or asyncio.Event()
        self.mock_stream = mock_stream or MockTimbreStream()
        self.model = model
        self.on_audio = on_audio
        self.turn0_disclosed = False
        self.last_ttfb_ms: float | None = None
        self.last_clause: str | None = None
        self.emitted_clauses: list[str] = []
        self._active_socket: Any | None = None

    @property
    def cancelled(self) -> bool:
        return self.barge_in_event.is_set() or self.tts_cancel_event.is_set()

    @staticmethod
    def _tls13_context() -> ssl.SSLContext:
        try:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.maximum_version = ssl.TLSVersion.TLSv1_3
            return context
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"unable to configure Timbre TLS 1.3: {exc}") from exc

    def clear_cancellation(self) -> None:
        """Allow the next assistant turn after a handled caller interruption."""

        self.barge_in_event.clear()
        self.tts_cancel_event.clear()

    @staticmethod
    def _validate_clause(text: str) -> str:
        cleaned = strip_ssml(text).strip()
        if not cleaned:
            raise ValueError("Timbre clause cannot be empty")
        return cleaned

    async def _notify_audio(self, payload: dict[str, Any]) -> None:
        if self.on_audio is None:
            return
        try:
            result = self.on_audio(payload)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Timbre audio observer failed: {exc}") from exc

    async def _stream_mock_clause(
        self, clause: str, *, turn_index: int
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            async for chunk in self.mock_stream.stream_clause(
                clause, turn_index=turn_index
            ):
                if self.cancelled:
                    return
                telemetry = dict(chunk.get("telemetry", {}))
                ttfb = float(
                    telemetry.get("timbre_ttfb_ms", telemetry.get("ttfb_ms", 80.0))
                )
                if self.last_ttfb_ms is None:
                    self.last_ttfb_ms = ttfb
                output = {
                    **chunk,
                    "clause": clause,
                    "tts_ttfb_ms": ttfb,
                    "telemetry": telemetry,
                }
                await self._notify_audio(output)
                yield output
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"mock Timbre clause failed: {exc}") from exc

    @staticmethod
    def _live_audio_payload(raw: Any) -> bytes | None:
        if isinstance(raw, bytes):
            return raw
        if not isinstance(raw, str):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid Timbre JSON frame: {exc}") from exc
        if not isinstance(data, dict):
            raise TypeError("Timbre JSON frame must be an object")
        encoded = data.get("audio") or data.get("pcm") or data.get("audio_base64")
        if not encoded:
            return None
        if isinstance(encoded, str):
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise RuntimeError(f"invalid Timbre audio payload: {exc}") from exc
        raise TypeError("Timbre audio payload must be base64 text")

    async def _stream_live_clause(
        self, clause: str
    ) -> AsyncIterator[dict[str, Any]]:
        started_ns = time.perf_counter_ns()
        try:
            headers = {"X-API-Key-ID": settings.get_active_api_key()}
            context = self._tls13_context()
            async with websockets.connect(
                settings.GNANI_TTS_WSS_URL,
                extra_headers=headers,
                ssl=context,
            ) as socket:
                self._active_socket = socket
                await socket.send(
                    json.dumps(
                        {
                            "model": self.model,
                            "text": clause,
                            "ssml": False,
                        },
                        ensure_ascii=False,
                    )
                )
                async for raw in socket:
                    if self.cancelled:
                        return
                    audio = self._live_audio_payload(raw)
                    if not audio:
                        continue
                    ttfb = (time.perf_counter_ns() - started_ns) / 1_000_000
                    if self.last_ttfb_ms is None:
                        self.last_ttfb_ms = ttfb
                    for offset in range(0, len(audio), PCM_FRAME_BYTES):
                        if self.cancelled:
                            return
                        frame = audio[offset : offset + PCM_FRAME_BYTES]
                        if len(frame) < PCM_FRAME_BYTES:
                            frame = frame.ljust(PCM_FRAME_BYTES, b"\x00")
                        payload = {
                            "type": "audio",
                            "audio": frame,
                            "clause": clause,
                            "tts_ttfb_ms": ttfb,
                            "telemetry": {
                                "transport_ms": 12.0,
                                "timbre_ttfb_ms": ttfb,
                            },
                        }
                        await self._notify_audio(payload)
                        yield payload
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError, websockets.WebSocketException) as exc:
            raise RuntimeError(f"live Timbre stream failed: {exc}") from exc
        finally:
            self._active_socket = None

    async def stream_clause(
        self, clause: str, *, turn_index: int = 1
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream one clause after enforcing disclosure, SSML, and cancellation."""

        try:
            cleaned = self._validate_clause(clause)
            if turn_index > 0 and not self.turn0_disclosed:
                raise RuntimeError("Turn-0 AI disclosure must be streamed first")
            if self.cancelled:
                return
            self.last_clause = cleaned
            self.last_ttfb_ms = None
            self.emitted_clauses.append(cleaned)
            if settings.USE_MOCK_GNANI:
                async for chunk in self._stream_mock_clause(
                    cleaned, turn_index=turn_index
                ):
                    yield chunk
            else:
                async for chunk in self._stream_live_clause(cleaned):
                    yield chunk
        except asyncio.CancelledError:
            raise
        except (RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Timbre clause guard failed: {exc}") from exc

    async def stream_tokens(
        self, token_stream: AsyncIterator[str], *, turn_index: int = 1
    ) -> AsyncIterator[dict[str, Any]]:
        """Chunk an Evon token stream and stream each completed clause."""

        try:
            async for clause in chunk_by_clause(token_stream):
                if self.cancelled:
                    return
                async for audio_chunk in self.stream_clause(
                    clause, turn_index=turn_index
                ):
                    yield audio_chunk
        except asyncio.CancelledError:
            raise
        except (RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Timbre token streaming failed: {exc}") from exc

    async def stream_text(
        self, text: str, *, turn_index: int = 1
    ) -> AsyncIterator[dict[str, Any]]:
        """Chunk a complete Evon response at safe boundaries and synthesize it."""

        if turn_index > 0 and not self.turn0_disclosed:
            raise RuntimeError("Turn-0 AI disclosure must be streamed first")
        if self.cancelled:
            return
        for clause in ClauseChunker().chunk(text):
            if self.cancelled:
                return
            async for chunk in self.stream_clause(clause, turn_index=turn_index):
                yield chunk

    async def synthesize(
        self, text: str, *, turn_index: int = 1
    ) -> list[dict[str, Any]]:
        """Collect a complete response for callers that do not need streaming."""

        return [
            chunk async for chunk in self.stream_text(text, turn_index=turn_index)
        ]

    async def stream_turn0_disclosure(self, language: str = "hi-IN") -> list[dict[str, Any]]:
        """Synthesize and mark the mandatory first-turn AI disclosure."""

        try:
            if self.turn0_disclosed:
                return []
            disclosure = self.DISCLOSURES.get(language, self.DISCLOSURES["hi-IN"])
            chunks = [
                chunk
                async for chunk in self.stream_clause(disclosure, turn_index=0)
            ]
            if not self.cancelled:
                self.turn0_disclosed = True
            return chunks
        except asyncio.CancelledError:
            raise
        except (RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Turn-0 disclosure failed: {exc}") from exc

    async def close(self) -> None:
        """Close an active live socket; mock streams need no teardown."""

        socket = self._active_socket
        self._active_socket = None
        if socket is None:
            return
        try:
            await socket.close()
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, websockets.WebSocketException):
            return
