"""Static frontend contract and backend WebSocket compatibility tests."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.media_gateway import app

FRONTEND = ROOT / "frontend"
WORKLET = FRONTEND / "audio_worklet.js"
MIXER = FRONTEND / "noise_mixer.ts"
DASHBOARD = FRONTEND / "telemetry_dashboard.tsx"


def _read(path: Path) -> str:
    assert path.exists(), f"missing frontend deliverable: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"empty frontend deliverable: {path}"
    return text


def test_frontend_files_exist_and_have_no_secret_or_cloud_endpoint() -> None:
    sources = "\n".join(_read(path) for path in (WORKLET, MIXER, DASHBOARD))
    assert "X-API-Key-ID" not in sources
    assert "wss://api.vachana.ai" not in sources
    assert "GNANI_API_KEY" not in sources
    assert "A/C XX4521" in sources
    assert "₹1,24,567.50" in sources
    assert "05/11/2026" in sources
    assert "2.5% p.a." in sources
    assert "phone number" not in sources.casefold()
    assert "aadhaar" not in sources.casefold()
    assert "pan number" not in sources.casefold()


def test_audio_worklet_enforces_pcm_and_barge_in_contract() -> None:
    source = _read(WORKLET)
    for marker in (
        'registerProcessor("artha-pcm-processor"',
        "AudioWorkletProcessor",
        "Int16Array",
        "FRAME_SAMPLES = 512",
        "FRAME_BYTES = 1024",
        'type: "pcm_frame"',
        'message.type === "enqueue_tts_pcm"',
        'type === "clear_audio"',
        'type: "barge_in_flushed"',
        "playbackQueue.length = 0",
    ):
        assert marker in source


def test_noise_mixer_contains_pstn_noise_and_gateway_protocol() -> None:
    source = _read(MIXER)
    for marker in (
        "class NoiseMixerEngine",
        "class VoiceSessionClient",
        'type NoiseProfile = "bazaar" | "traffic" | "pstn_hiss" | "clean"',
        'type VadMode = "FAST_TURN" | "DIGIT_CAPTURE"',
        "highpass.frequency.value = 300",
        "lowpass.frequency.value = 3400",
        "setPstn8kHzEnabled",
        "setNoiseSnrDb",
        "clamp(snrDb, 0, 20)",
        "createProceduralNoiseBuffer",
        "bazaar",
        "traffic",
        "pstn_hiss",
        'event: "set_vad_mode"',
        'event: "update_bias"',
        'event.event === "clear_audio"',
        "/ws/call",
    ):
        assert marker in source


def test_dashboard_matches_screen1_tokens_accessibility_and_phase5_shell() -> None:
    source = _read(DASHBOARD)
    for marker in (
        "#090D16",
        "#0F172A",
        "#1E293B",
        "#334155",
        "#F8FAFC",
        "#94A3B8",
        "#3B82F6",
        "#10B981",
        "#F59E0B",
        "#F43F5E",
        "bg-slate-950",
        "tabular-nums",
        "AI Assistant Disclosure: Active on Turn 0",
        "<20 ms",
        "~200 ms",
        "~302 ms",
        "<100–150 ms",
        "~622 ms",
        "800 ms",
        "Fast-Turn 350ms silence",
        "Digit-Capture 800ms silence",
        "x-min-speech-ms: 250",
        "x-min-segment-ms: 120",
        "confidence &gt;= 0.75",
        "EMI",
        "PMFBY",
        "KCC",
        "IRDAI",
        "किस्त",
        "Human Agent Handoff Context",
        "Screen 2: Supervisor Batch Audit Tab",
        "/stt/v3/batch/jobs",
        "with_denoise=True",
        "enable_thinking=True",
        'aria-live="polite"',
        "Empty state",
        "Loading / Streaming",
        "Error / Fallback",
        "PASS (<800ms)",
    ):
        assert marker in source


def test_frontend_control_payloads_align_with_gateway_contract() -> None:
    with TestClient(app) as client, client.websocket_connect(
        "/ws/call?sample_rate=16000&vad_mode=FAST_TURN"
    ) as websocket:
        disclosure = websocket.receive_json()
        assert disclosure["event"] == "ai_disclosure"
        assert disclosure["verified"] is True
        for _ in range(14):
            assert websocket.receive_json()["event"] == "audio"

        websocket.send_json({"event": "set_vad_mode", "mode": "DIGIT_CAPTURE"})
        vad_event = websocket.receive_json()
        assert vad_event["event"] == "telemetry"
        assert vad_event["active_vad_mode"] == "DIGIT_CAPTURE"
        assert vad_event["x-min-silence-ms"] == "800"

        websocket.send_json(
            {
                "event": "update_bias",
                "words": ["EMI", "PMFBY", "KCC", "IRDAI", "किस्त"],
            }
        )
        bias_event = websocket.receive_json()
        assert bias_event["event"] == "telemetry"
        assert bias_event["bias_update"]["type"] == "update_settings"

        websocket.send_bytes(b"\x00" * 1024)
        first_voice_event = websocket.receive_json()
        assert first_voice_event["event"] == "clear_audio"
        assert first_voice_event["latency_ms"] < 30
