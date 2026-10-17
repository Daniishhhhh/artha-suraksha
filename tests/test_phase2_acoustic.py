"""Phase 2 pacing, Prisma control, and FastAPI WebSocket acceptance tests."""

from __future__ import annotations

import asyncio
import ssl
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import DIGIT_CAPTURE_VAD, FAST_TURN_VAD, settings
from backend.media_gateway import PCMFramePacer, app
from backend.mock_replay import PCM_FRAME_BYTES
from backend.prisma_controller import PrismaController


@pytest.mark.asyncio
async def test_pacer_slices_arbitrary_chunks_into_exact_frames() -> None:
    emitted: list[bytes] = []
    pacer = PCMFramePacer(lambda frame: emitted.append(frame), sample_rate=16000)
    pacer.ingest_chunk(b"a" * 320)
    pacer.ingest_chunk(b"b" * 3200)
    pacer.ingest_chunk(b"c" * 5000)

    while pacer.buffered_bytes >= PCM_FRAME_BYTES:
        await pacer._dispatch_one()

    assert len(emitted) == 8
    assert all(len(frame) == PCM_FRAME_BYTES for frame in emitted)
    assert emitted[0] == b"a" * 320 + b"b" * 704
    assert pacer.buffered_bytes == 328


@pytest.mark.asyncio
async def test_pacer_uses_monotonic_32ms_and_64ms_cadence_without_bursting() -> None:
    for sample_rate, expected_interval in ((16000, 0.032), (8000, 0.064)):
        dispatch_times: list[float] = []

        async def on_frame(
            frame: bytes, dispatch_times: list[float] = dispatch_times
        ) -> None:
            assert len(frame) == PCM_FRAME_BYTES
            dispatch_times.append(time.monotonic())

        pacer = PCMFramePacer(on_frame, sample_rate=sample_rate)
        pacer.ingest_chunk(b"x" * (PCM_FRAME_BYTES * 3))
        await pacer.start()
        await asyncio.sleep(expected_interval * 2.35)
        await pacer.stop()

        assert len(dispatch_times) >= 2
        assert dispatch_times[1] - dispatch_times[0] >= expected_interval * 0.75
        assert dispatch_times[1] - dispatch_times[0] < expected_interval * 1.8


@pytest.mark.asyncio
async def test_pacer_flush_preserves_frame_contract() -> None:
    emitted: list[bytes] = []
    pacer = PCMFramePacer(
        lambda frame: emitted.append(frame),
        sample_rate=8000,
        pad_partial_on_flush=True,
    )
    pacer.ingest_chunk(b"tail" * 100)
    padded = await pacer.flush()
    assert padded is not None
    assert len(padded) == PCM_FRAME_BYTES
    assert padded.startswith(b"tail")
    assert pacer.buffered_bytes == 0
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_prisma_vad_handover_digit_accumulator_and_bias_update() -> None:
    controller = PrismaController()
    await controller.connect()

    assert controller.active_vad_headers["x-min-silence-ms"] == "350"
    assert controller.active_vad_headers["x-min-speech-ms"] == "250"
    assert controller.active_vad_headers["x-min-segment-ms"] == "120"
    assert controller.active_vad_headers == dict(FAST_TURN_VAD)

    state = await controller.set_vad_mode("DIGIT_CAPTURE")
    assert state["active_vad_mode"] == "DIGIT_CAPTURE"
    assert state["x-min-silence-ms"] == "800"
    assert controller.active_vad_headers == dict(DIGIT_CAPTURE_VAD)

    assert controller.merge_digit_segments(["A/C XX45", "21"]) == "A/C XX4521"
    assert controller.digit_transcript == "A/C XX4521"

    update = await controller.update_bias_settings(
        ["EMI", "PMFBY", "KCC", "IRDAI", "किस्त"], 1.5
    )
    assert update == {
        "type": "update_settings",
        "x-bias-list": ["EMI", "PMFBY", "KCC", "IRDAI", "किस्त"],
        "x-bias-score": 1.5,
    }
    assert controller.control_frames[-1] == update
    assert controller.telemetry_state()["active_bias_list"] == update["x-bias-list"]
    await controller.close()


@pytest.mark.asyncio
async def test_live_prisma_handshake_is_tls13_and_header_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captures: list[tuple[str, dict[str, object]]] = []

    class FakeSocket:
        async def send(self, payload: object) -> None:
            return None

        async def close(self) -> None:
            return None

    async def fake_connect(url: str, **kwargs: object) -> FakeSocket:
        captures.append((url, kwargs))
        return FakeSocket()

    monkeypatch.setattr(settings, "USE_MOCK_GNANI", False)
    monkeypatch.setattr(settings, "GNANI_API_KEY_DEV", SecretStr("synthetic-dev-key"))
    monkeypatch.setattr(
        "backend.prisma_controller.websockets.connect", fake_connect
    )

    controller = PrismaController()
    await controller.connect()
    assert len(captures) == 2
    assert all(url == settings.GNANI_STT_WSS_URL for url, _ in captures)
    for _, kwargs in captures:
        headers = kwargs["extra_headers"]
        assert isinstance(headers, dict)
        assert headers["X-API-Key-ID"] == "synthetic-dev-key"
        assert headers["x-min-speech-ms"] == "250"
        assert headers["x-min-segment-ms"] == "120"
        assert headers["x-speech-confidence"] == "0.75"
        assert kwargs["ssl"].minimum_version == ssl.TLSVersion.TLSv1_3
        assert kwargs["ssl"].maximum_version == ssl.TLSVersion.TLSv1_3
    assert captures[0][1]["extra_headers"]["x-min-silence-ms"] == "350"  # type: ignore[index]
    assert captures[1][1]["extra_headers"]["x-min-silence-ms"] == "800"  # type: ignore[index]
    await controller.close()


@pytest.mark.asyncio
async def test_prisma_processing_dispatches_barge_in_under_30ms() -> None:
    callback_latencies: list[float] = []

    async def on_barge_in(latency_ms: float) -> None:
        callback_latencies.append(latency_ms)

    controller = PrismaController(on_barge_in=on_barge_in)
    await controller.connect()
    await controller.simulate_mock_speech()
    events = await controller.receive_loop()

    assert events[0]["type"] == "processing"
    assert events[1]["status"] == "completed"
    assert callback_latencies
    assert callback_latencies[0] < 30.0
    assert controller.last_barge_in_latency_ms is not None
    assert controller.last_barge_in_latency_ms < 30.0
    assert controller.tts_cancel_event.is_set()
    await controller.close()


@pytest.mark.asyncio
async def test_prisma_rejects_misaligned_audio() -> None:
    controller = PrismaController()
    with pytest.raises(ValueError, match="exactly 1024"):
        await controller.send_audio_frame(b"not-aligned")
    await controller.close()


def test_health_endpoint_reports_mock_and_synthetic_database() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["active_mode"] == "mock"
    assert payload["active_key_profile"] == settings.ACTIVE_KEY_PROFILE
    assert payload["database_status"] == "synthetic-local-ready"


def test_ws_call_streams_barge_in_transcript_and_control_telemetry() -> None:
    with TestClient(app) as client, client.websocket_connect(
        "/ws/call?sample_rate=16000&vad_mode=FAST_TURN"
    ) as websocket:
        websocket.send_json({"event": "set_vad_mode", "mode": "DIGIT_CAPTURE"})
        vad_update = websocket.receive_json()
        assert vad_update["event"] == "telemetry"
        assert vad_update["active_vad_mode"] == "DIGIT_CAPTURE"
        assert vad_update["x-min-silence-ms"] == "800"

        websocket.send_json(
            {
                "event": "update_bias",
                "words": ["EMI", "PMFBY", "KCC", "IRDAI", "किस्त"],
            }
        )
        bias_update = websocket.receive_json()
        assert bias_update["event"] == "telemetry"
        assert bias_update["bias_update"]["type"] == "update_settings"
        assert bias_update["active_bias_score"] == 1.5

        # Variable-sized client chunks are reassembled and paced before the
        # controller sees exactly one 1,024-byte frame per tick.
        websocket.send_bytes(b"a" * 320)
        websocket.send_bytes(b"b" * 704)
        websocket.send_bytes(b"c" * 5000)

        clear_audio = websocket.receive_json()
        transcript = websocket.receive_json()
        assert clear_audio["event"] == "clear_audio"
        assert clear_audio["latency_ms"] < 30.0
        assert transcript["event"] == "transcript"
        assert transcript["status"] == "completed"
        assert "A/C XX4521" in transcript["text"]

        telemetry = websocket.receive_json()
        assert telemetry["event"] == "telemetry"
        assert telemetry["active_vad_mode"] == "DIGIT_CAPTURE"


def test_ws_call_rejects_invalid_control_json_without_network() -> None:
    with TestClient(app) as client, client.websocket_connect(
        "/ws/call"
    ) as websocket:
        websocket.send_json({"event": "set_vad_mode"})
        error = websocket.receive_json()
        assert error["event"] == "error"
        assert error["error"] == "invalid control message"
