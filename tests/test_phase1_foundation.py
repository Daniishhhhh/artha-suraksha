"""Acceptance tests for the Artha-Suraksha Phase 1 foundation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import (
    DEFAULT_BIAS_LIST,
    DEFAULT_BIAS_SCORE,
    DIGIT_CAPTURE_VAD,
    FAST_TURN_VAD,
    Settings,
    settings,
)
from backend.database import (
    AccountStatusInput,
    ClaimPayoutInput,
    EmiDeferralInput,
    HumanHandoffInput,
    calculate_claim_payout,
    get_account_status,
    init_and_seed_db,
    schedule_emi_deferral,
    trigger_human_handoff,
)
from backend.mock_replay import (
    AI_DISCLOSURE_TEXT,
    PCM_FRAME_BYTES,
    MockPrismaStream,
    MockReplayManager,
    MockTimbreStream,
)


def test_configuration_defaults_vad_biases_and_key_profiles() -> None:
    assert settings.USE_MOCK_GNANI is True
    assert FAST_TURN_VAD["x-min-silence-ms"] == "350"
    assert DIGIT_CAPTURE_VAD["x-min-silence-ms"] == "800"
    assert DEFAULT_BIAS_LIST == ["EMI", "PMFBY", "KCC", "IRDAI", "किस्त"]
    assert DEFAULT_BIAS_SCORE == 1.5

    with pytest.raises(TypeError):
        FAST_TURN_VAD["x-min-silence-ms"] = "0"  # type: ignore[index]

    dev = Settings(
        GNANI_API_KEY_DEV="dev-placeholder",
        ACTIVE_KEY_PROFILE="DEV",
    )
    telemetry = Settings(
        GNANI_API_KEY_TELEMETRY="telemetry-placeholder",
        ACTIVE_KEY_PROFILE="TELEMETRY",
    )
    assert dev.get_active_api_key() == "dev-placeholder"
    assert telemetry.get_active_api_key() == "telemetry-placeholder"

    demo = Settings(
        GNANI_API_KEY_DEMO="demo-placeholder",
        ACTIVE_KEY_PROFILE="DEMO",
    )
    with pytest.raises(RuntimeError, match="disabled"):
        demo.get_active_api_key()


def test_pydantic_input_models_reject_invalid_values() -> None:
    with pytest.raises(ValidationError):
        AccountStatusInput(account_id="")
    with pytest.raises(ValidationError):
        ClaimPayoutInput(policy_id="PMFBY-2026-8841", crop_loss_pct=-1)
    with pytest.raises(ValidationError):
        EmiDeferralInput(
            account_id="A/C XX4521",
            requested_date="2026-11-05",
            reason="temporary distress",
        )
    with pytest.raises(ValidationError):
        HumanHandoffInput(
            account_id="A/C XX4521",
            reason="caller asks for a person",
            confidence_score=1.5,
        )


@pytest.mark.asyncio
async def test_database_seed_anchor_and_all_tools(tmp_path: Path) -> None:
    db_path = tmp_path / "synthetic.sqlite"
    assert await init_and_seed_db(db_path) == 50

    anchor = await get_account_status("A/C XX4521", db_path)
    assert anchor["success"] is True
    assert anchor["outstanding_balance_formatted"] == "₹1,24,567.50"
    assert anchor["next_emi_due_date"] == "05/11/2026"
    assert anchor["interest_rate_formatted"] == "2.5% p.a."
    assert anchor["preferred_language"] == "hi-IN"
    assert anchor["claim_status"] == "UNDER_REVIEW"

    payout = await calculate_claim_payout(
        "PMFBY-2026-8841", 50.0, db_path
    )
    assert payout["success"] is True
    assert payout["eligible"] is True
    assert payout["payout_amount_formatted"] == "₹62,283.75"

    below_threshold = await calculate_claim_payout(
        "PMFBY-2026-8841", 32.9, db_path
    )
    assert below_threshold["eligible"] is False
    assert below_threshold["payout_amount_inr"] == 0.0

    deferral = await schedule_emi_deferral(
        "A/C XX4521",
        "15/12/2026",
        "temporary crop income distress",
        db_path,
    )
    assert deferral["success"] is True
    assert deferral["status"] == "scheduled"
    assert deferral["interest_rate_formatted"] == "2.5% p.a."
    assert deferral["nach_mandate_action"].startswith("PRESERVE_MANDATE")

    handoff = await trigger_human_handoff(
        "A/C XX4521",
        "caller disputes claim rejection",
        0.61,
        db_path,
    )
    assert handoff["success"] is True
    assert handoff["mandatory_escalation"] is True
    assert handoff["grievance_ticket_id"] == "HND-A-C-XX4521"

    invalid_tool_result = await calculate_claim_payout(
        "PMFBY-2026-8841", 101.0, db_path
    )
    assert invalid_tool_result["success"] is False
    assert invalid_tool_result["status"] == "error"


@pytest.mark.asyncio
async def test_mock_stt_tts_turn_and_frame_contract(tmp_path: Path) -> None:
    prisma = MockPrismaStream()
    with pytest.raises(ValueError, match="exactly 1024"):
        await prisma.send_frame(b"bad-frame")

    await prisma.send_frame(bytes(PCM_FRAME_BYTES))
    await prisma.simulate_speech()
    events = [event async for event in prisma]
    assert events[0]["type"] == "processing"
    assert events[1]["status"] == "completed"
    assert events[1]["transcript"] == "Mera A/C XX4521 ka EMI kab due hai?"
    assert events[1]["account_reference"] == "A/C XX4521"

    await prisma.simulate_speech()
    second_events = [event async for event in prisma]
    assert "कृपया" in second_events[1]["text"]

    timbre = MockTimbreStream()
    with pytest.raises(ValueError, match="Turn-0"):
        await timbre.synthesize("Aapka balance yahan hai.", turn_index=0)
    chunks = await timbre.synthesize(AI_DISCLOSURE_TEXT, turn_index=0)
    assert chunks
    assert all(len(chunk["audio"]) == PCM_FRAME_BYTES for chunk in chunks)
    assert all(chunk["pcm_encoding"] == "s16le" for chunk in chunks)
    assert chunks[0]["telemetry"]["transport_ms"] < 20
    assert chunks[0]["telemetry"]["stt_ms"] == 200.0
    assert chunks[0]["telemetry"]["evon_ttft_ms"] == 302.0
    assert chunks[0]["telemetry"]["timbre_ttfb_ms"] < 100

    manager = MockReplayManager(tmp_path / "replays")
    payload = {"type": "completed", "text": "synthetic"}
    pcm = b"\x00" * PCM_FRAME_BYTES
    manager.save_exchange("turn-01", payload, pcm)
    assert manager.replay_json("turn-01") == payload
    assert manager.replay_pcm("turn-01") == pcm


def test_policy_context_is_utf8_and_contains_required_clauses() -> None:
    policy_path = ROOT / "backend" / "policy_context.md"
    assert policy_path.exists()
    text = policy_path.read_text(encoding="utf-8")
    assert len(text.strip()) > 1000
    for marker in ("PMFBY", "EMI", "IRDAI", "RBI", "किस्त", "फसल", "Hinglish"):
        assert marker in text
    for tool in (
        "get_account_status",
        "calculate_claim_payout",
        "schedule_emi_deferral",
        "trigger_human_handoff",
    ):
        assert tool in text
    for financial_string in (
        "₹1,24,567.50",
        "A/C XX4521",
        "05/11/2026",
        "2.5% p.a.",
    ):
        assert financial_string in text
