"""Offline Phase 5 procurement benchmark checks.

Every check uses the deterministic mock path.  The suite intentionally covers
the six acceptance dimensions without spending a live STT or Evon credit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.evon_orchestrator import EvonOrchestrator
from backend.media_gateway import PCMFramePacer, app
from backend.mock_replay import PCM_FRAME_BYTES
from backend.prisma_controller import PrismaController
from backend.timbre_streamer import TimbreStreamer, contains_ssml


@pytest.mark.asyncio
async def test_check_1_noisy_batch_lifecycle_and_transcript() -> None:
    assert settings.USE_MOCK_GNANI is True
    controller = PrismaController()
    created = await controller.create_batch_job(
        "synthetic_noisy_call_xx4521.wav", b"synthetic-noise"
    )
    assert created["status"] == "CREATED"
    assert created["with_denoise"] is True
    assert created["languages"] == ["hi-IN", "en-IN", "kn-IN"]
    started = await controller.start_batch_job(created["job_id"])
    assert started["status"] == "STARTED"
    result = await controller.poll_batch_job(created["job_id"], poll_interval_s=0)
    assert result["history"] == ["CREATED", "STARTED", "POLLING", "COMPLETED"]
    assert result["verbatim_transcript"]
    assert result["with_denoise"] is True
    assert "नमस्ते" in result["verbatim_transcript"]
    assert "PMFBY-2026-8841" in result["verbatim_transcript"]

    with TestClient(app) as client:
        response = client.post(
            "/api/batch/audit",
            json={
                "file_name": "endpoint-noisy.wav",
                "poll_interval_s": 0,
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["history"] == [
        "CREATED",
        "STARTED",
        "POLLING",
        "EVON_THINKING_AUDIT",
        "COMPLETED",
    ]
    assert body["compliance_report"]["denoised"] is True
    with TestClient(app) as client:
        current = client.get(f"/api/batch/jobs/{body['job_id']}")
    assert current.status_code == 200
    assert current.json()["status"] == "COMPLETED"
    assert current.json()["compliance_report"]["job_id"] == body["job_id"]

    with TestClient(app) as client:
        file_response = client.post(
            "/api/batch/audit",
            files={"file": ("multipart-noisy.wav", b"synthetic-audio", "audio/wav")},
        )
    assert file_response.status_code == 200, file_response.text
    assert file_response.json()["compliance_report"]["denoised"] is True


@pytest.mark.asyncio
async def test_check_2_language_metadata_and_denoise_contract() -> None:
    controller = PrismaController()
    result = await controller.run_batch_stt_job(
        "languages.wav", b"audio", languages=["hi-IN", "en-IN", "kn-IN"], poll_interval_s=0
    )
    assert result["detected_languages"] == ["hi-IN", "en-IN", "kn-IN"]
    assert result["nominal_poll_interval_s"] == 10.0
    with TestClient(app) as client:
        wer = client.get("/api/benchmark/scorecard").json()["wer_benchmark"]
    assert wer["vad_presets"] == {"FAST_TURN": 350, "DIGIT_CAPTURE": 800}
    assert wer["reduction_pct"] >= 30.0
    assert "A/C XX4521" in result["verbatim_transcript"]
    with pytest.raises(RuntimeError, match="with_denoise"):
        await controller.create_batch_job("bad.wav", b"audio", with_denoise=False)


def test_check_3_p95_turnaround_and_barge_in_scorecard() -> None:
    assert PCMFramePacer(sample_rate=8000).interval == 0.064
    assert PCMFramePacer(sample_rate=16000).interval == 0.032
    with TestClient(app) as client:
        scorecard = client.get("/api/benchmark/scorecard")
    assert scorecard.status_code == 200
    body = scorecard.json()
    assert body["p95_total_turnaround_ms"] < body["turnaround_threshold_ms"]
    assert body["barge_in_latency_ms"] < 30
    assert body["zero_overlap_barge_in_threshold_ms"] == 30.0
    assert body["audio_resilience"]["frame_bytes"] == PCM_FRAME_BYTES
    assert body["audio_resilience"]["frame_duration_ms"] == 64.0
    assert body["wer_benchmark"]["x-bias-score"] == 1.5
    assert body["wer_benchmark"]["tuned_wer_pct"] < body["wer_benchmark"]["baseline_wer_pct"]
    assert len(body["checks"]) == 6


def test_check_3_live_mock_waterfall_is_under_800ms() -> None:
    with TestClient(app) as client, client.websocket_connect(
        "/ws/call?sample_rate=8000&vad_mode=FAST_TURN"
    ) as websocket:
        assert websocket.receive_json()["event"] == "ai_disclosure"
        for _ in range(6):
            assert websocket.receive_json()["event"] == "audio"
        websocket.send_bytes(b"x" * PCM_FRAME_BYTES)
        telemetry: dict[str, object] | None = None
        for _ in range(64):
            message = websocket.receive_json()
            if message.get("event") == "telemetry" and "total_turnaround_ms" in message:
                telemetry = message
                break
        assert telemetry is not None
        assert telemetry["transport_ms"] < 20
        assert telemetry["prisma_stt_ms"] == 200.0
        assert telemetry["evon_ttft_ms"] == 302.0
        assert telemetry["timbre_ttfb_ms"] < 150
        assert telemetry["total_turnaround_ms"] == 622.0


@pytest.mark.asyncio
async def test_check_4_confidence_handoff_and_pii_guardrail(tmp_path: Path) -> None:
    orchestrator = EvonOrchestrator(db_path=tmp_path / "handoff.sqlite")
    result = await orchestrator.run_turn(
        "The claim was rejected; please send A/C XX4521 to a human officer.",
        asr_confidence=0.5,
    )
    assert result.handoff_packet is not None
    assert {
        "account_id",
        "grievance_ticket_id",
        "reason",
        "confidence_score",
        "mandatory_escalation",
        "escalation_reason",
    }.issubset(result.handoff_packet)
    assert result.handoff_packet["account_id"] == "A/C XX4521"
    assert result.handoff_packet["confidence_score"] < 0.65
    assert result.handoff_packet["grievance_ticket_id"] == "HND-A-C-XX4521"
    assert not orchestrator._audit_synthetic_pii_compliant("Aadhaar 1234 5678 9012")
    assert not orchestrator._audit_synthetic_pii_compliant("PAN ABCDE1234F")
    assert not orchestrator._audit_synthetic_pii_compliant("mobile 9876543210")


@pytest.mark.asyncio
async def test_check_5_policy_grounding_entities_and_audit(tmp_path: Path) -> None:
    orchestrator = EvonOrchestrator(db_path=tmp_path / "audit.sqlite")
    assert "FULL POLICY CONTEXT" in orchestrator.system_prompt
    with TestClient(app) as client:
        assert client.get("/api/benchmark/scorecard").json()["policy_context_window_tokens"] == 128000
    tool_results = [
        await orchestrator.execute_tool("get_account_status", {"account_id": "A/C XX4521"}),
        await orchestrator.execute_tool(
            "calculate_claim_payout",
            {"policy_id": "PMFBY-2026-8841", "crop_loss_pct": 50.0},
        ),
        await orchestrator.execute_tool(
            "schedule_emi_deferral",
            {
                "account_id": "A/C XX4521",
                "requested_date": "15/12/2026",
                "reason": "synthetic crop income distress",
            },
        ),
        await orchestrator.execute_tool(
            "trigger_human_handoff",
            {
                "account_id": "A/C XX4521",
                "reason": "synthetic low confidence review",
                "confidence_score": 0.5,
            },
        ),
    ]
    assert [result["success"] for result in tool_results] == [True, True, True, True]
    assert tool_results[0]["outstanding_balance_formatted"] == "₹1,24,567.50"
    controller = PrismaController()
    result = await controller.run_batch_stt_job("audit.wav", b"audio", poll_interval_s=0)
    report = await orchestrator.generate_compliance_audit(result["job_id"], result)
    assert report.ai_disclosure_verified is True
    assert report.synthetic_pii_compliant is True
    assert report.model_dump().keys() == {
        "job_id",
        "denoised",
        "detected_languages",
        "verbatim_transcript",
        "ai_disclosure_verified",
        "synthetic_pii_compliant",
        "extracted_entities",
        "emi_or_claim_mutations",
        "handoff_triggered",
        "sentiment",
        "enable_thinking_used",
        "reasoning_trace",
    }
    assert report.extracted_entities == {
        "account_id": "A/C XX4521",
        "balance": "₹1,24,567.50",
        "due_date": "05/11/2026",
        "interest_rate": "2.5% p.a.",
        "policy_id": "PMFBY-2026-8841",
    }
    assert report.emi_or_claim_mutations
    assert report.enable_thinking_used is True

    negative = await orchestrator.generate_compliance_audit(
        "negative-audit",
        {
            "denoised": True,
            "detected_languages": ["hi-IN", "en-IN", "kn-IN"],
            "verbatim_transcript": "Account 9876543210 has a claim, but no disclosure was spoken.",
        },
    )
    assert negative.ai_disclosure_verified is False
    assert negative.synthetic_pii_compliant is False

    timbre = TimbreStreamer()
    await timbre.stream_turn0_disclosure()
    await timbre.synthesize(
        "Balance ₹1,24,567.50 for A/C XX4521 is due 05/11/2026 at 2.5% p.a."
    )
    assert all(not contains_ssml(clause) for clause in timbre.emitted_clauses)
    assert any("₹1,24,567.50" in clause for clause in timbre.emitted_clauses)


def test_check_6_sovereign_cost_efficiency_and_security_audit() -> None:
    with TestClient(app) as client:
        response = client.post("/api/batch/audit", json={"file_name": "audit.wav"})
        scorecard = client.get("/api/benchmark/scorecard").json()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["history"] == [
        "CREATED",
        "STARTED",
        "POLLING",
        "EVON_THINKING_AUDIT",
        "COMPLETED",
    ]
    assert body["compliance_report"]["enable_thinking_used"] is True
    assert scorecard["api_credits_spent_in_mock"] == 0
    assert scorecard["evon_cost_index"]["gnani_evon_v3_3_baseline"] == 100
    assert scorecard["evon_cost_index"]["DeepSeek-V4-Flash"] == 219
    assert scorecard["evon_cost_index"]["gpt-5.4-nano"] == 527
    assert scorecard["evon_cost_index"]["gpt-5.4-mini"] == 1754
    assert scorecard["indic_token_fertility_reduction_pct"] == 38.6
    assert scorecard["sqlite_tool_count"] == 4

    source_paths = [
        *ROOT.joinpath("backend").rglob("*.py"),
        *ROOT.joinpath("frontend").glob("*.ts"),
        *ROOT.joinpath("frontend").glob("*.tsx"),
        *ROOT.joinpath("frontend").glob("*.js"),
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    assert not re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", source)
    assert not re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", source)
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", source)
    assert "GNANI_API_KEY_DEMO=" not in source
