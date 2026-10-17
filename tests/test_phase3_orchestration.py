"""Phase 3 Evon orchestration, Timbre streaming, and closed-loop tests."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.evon_orchestrator import EvonOrchestrator
from backend.media_gateway import app
from backend.timbre_streamer import ClauseChunker, TimbreStreamer


def test_clause_chunker_protects_indian_financial_strings() -> None:
    text = "आपका बकाया ₹1,24,567.50 है, और दर 2.5% p.a. है। अगली EMI 05/11/2026 को है?"
    clauses = ClauseChunker().chunk(text)
    assert clauses == [
        "आपका बकाया ₹1,24,567.50 है,",
        "और दर 2.5% p.a. है।",
        "अगली EMI 05/11/2026 को है?",
    ]
    assert all("₹1,24,567.50" not in clause or clause.count("₹1,24,567.50") == 1 for clause in clauses)
    assert "2.5% p.a." in clauses[1]
    assert "05/11/2026" in clauses[2]


def test_clause_chunker_strips_ssml_but_preserves_inline_mood_markers() -> None:
    clauses = ClauseChunker().chunk(
        "<speak>[calm] Balance <say-as interpret-as='cardinal'>₹1,24,567.50</say-as>.</speak>"
    )
    assert clauses == ["[calm] Balance ₹1,24,567.50."]
    assert all("<" not in clause and ">" not in clause for clause in clauses)


@pytest.mark.asyncio
async def test_timbre_requires_turn0_and_emits_disclosure() -> None:
    streamer = TimbreStreamer()
    with pytest.raises(RuntimeError, match="Turn-0"):
        await streamer.synthesize("Aapka balance hai.")

    disclosure = await streamer.stream_turn0_disclosure("hi-IN")
    assert disclosure
    assert streamer.turn0_disclosed is True
    assert all(len(chunk["audio"]) == 1024 for chunk in disclosure)

    response = await streamer.synthesize(
        "Aapka balance <speak>₹1,24,567.50</speak> hai, rate 2.5% p.a. hai।"
    )
    assert response
    assert all("<speak>" not in clause for clause in streamer.emitted_clauses)
    assert any("₹1,24,567.50" in clause for clause in streamer.emitted_clauses)


@pytest.mark.asyncio
async def test_timbre_barge_in_stops_remaining_clauses_under_30ms() -> None:
    barge_in = asyncio.Event()
    streamer = TimbreStreamer(barge_in_event=barge_in)
    await streamer.stream_turn0_disclosure()
    first_chunk_time: list[float] = []

    async def stop_after_first(chunk: dict[str, object]) -> None:
        if not first_chunk_time:
            first_chunk_time.append(time.perf_counter())
            barge_in.set()

    streamer.on_audio = stop_after_first
    started = time.perf_counter()
    chunks = await streamer.synthesize(
        "पहला clause है। दूसरा clause कभी नहीं आना चाहिए। तीसरा भी नहीं।"
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert chunks
    assert len(streamer.emitted_clauses) == 2
    assert elapsed_ms < 30.0


@pytest.mark.asyncio
async def test_evon_loads_policy_routes_thinking_and_executes_all_tools(
    tmp_path: Path,
) -> None:
    orchestrator = EvonOrchestrator(db_path=tmp_path / "phase3.sqlite")
    assert "FULL POLICY CONTEXT" in orchestrator.system_prompt
    assert "PMFBY" in orchestrator.policy_context
    assert len(orchestrator.policy_context) > 1000

    account = await orchestrator.run_turn("Mera A/C XX4521 ka EMI status batao")
    assert account.enable_thinking is False
    assert orchestrator.last_request["temperature"] == 0.6
    assert orchestrator.last_request["top_p"] == 0.95
    assert orchestrator.last_request["extra_body"] == {"enable_thinking": False}
    assert account.tool_calls[0]["name"] == "get_account_status"
    assert "₹1,24,567.50" in account.text
    assert "05/11/2026" in account.text
    assert "2.5% p.a." in account.text

    claim = await orchestrator.run_turn("PMFBY crop loss is 50%", disputed_claim=True)
    assert claim.enable_thinking is True
    assert claim.filler_clause is not None
    assert claim.tool_calls[0]["name"] == "calculate_claim_payout"

    deferral = await orchestrator.run_turn(
        "Please defer my EMI for A/C XX4521 to 15/12/2026"
    )
    assert deferral.tool_calls[0]["name"] == "schedule_emi_deferral"
    assert deferral.tool_results[0]["status"] == "scheduled"

    handoff = await orchestrator.run_turn(
        "I dispute the rejection; send me to a human officer",
        asr_confidence=0.61,
    )
    assert handoff.tool_calls[0]["name"] == "trigger_human_handoff"
    assert handoff.handoff_packet is not None
    assert handoff.handoff_packet["mandatory_escalation"] is True


@pytest.mark.asyncio
async def test_evon_controller_signal_updates_digit_vad_and_bias(tmp_path: Path) -> None:
    class ControllerSpy:
        def __init__(self) -> None:
            self.modes: list[str] = []
            self.bias: list[tuple[list[str], float]] = []

        async def set_vad_mode(self, mode: str) -> None:
            self.modes.append(mode)

        async def update_bias_settings(self, words: list[str], score: float) -> None:
            self.bias.append((words, score))

    spy = ControllerSpy()
    orchestrator = EvonOrchestrator(
        db_path=tmp_path / "signals.sqlite", controller=spy
    )
    await orchestrator.run_turn("Please tell me my account number and EMI status")
    assert spy.modes == ["DIGIT_CAPTURE"]
    assert spy.bias and "EMI" in spy.bias[0][0]


def test_gateway_emits_turn0_and_closed_loop_voice_turn() -> None:
    with TestClient(app) as client, client.websocket_connect(
        "/ws/call?sample_rate=16000&vad_mode=FAST_TURN"
    ) as websocket:
        first = websocket.receive_json()
        assert first["event"] == "ai_disclosure"
        assert first["turn"] == 0
        assert first["verified"] is True
        # Drain the disclosure audio frames before sending the test utterance.
        for _ in range(6):
            disclosure_audio = websocket.receive_json()
            assert disclosure_audio["event"] == "audio"

        websocket.send_bytes(b"x" * 1024)
        messages: list[dict[str, object]] = []
        deadline = time.time() + 2
        while time.time() < deadline:
            message = websocket.receive_json()
            messages.append(message)
            if message.get("event") == "telemetry" and "total_turnaround_ms" in message:
                break

        assert any(message.get("event") == "clear_audio" for message in messages)
        transcript = next(message for message in messages if message.get("event") == "transcript")
        assert "A/C XX4521" in transcript["text"]
        audio = next(message for message in messages if message.get("event") == "audio")
        assert audio["audio_base64"]
        waterfall = next(
            message
            for message in messages
            if message.get("event") == "telemetry"
            and "total_turnaround_ms" in message
        )
        assert waterfall["total_turnaround_ms"] < 800
        assert waterfall["evon_ttft_ms"] >= 0
        assert waterfall["timbre_ttfb_ms"] < 100
