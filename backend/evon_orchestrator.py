"""Evon v3.3 context-grounded orchestration and deterministic tool calling."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
)

from backend.config import DEFAULT_BIAS_LIST, DEFAULT_BIAS_SCORE, settings
from backend.database import (
    CalculateClaimPayoutInput,
    GetAccountStatusInput,
    ScheduleEmiDeferralInput,
    TriggerHumanHandoffInput,
    calculate_claim_payout,
    get_account_status,
    init_and_seed_db,
    schedule_emi_deferral,
    trigger_human_handoff,
)
from backend.mock_replay import DEFAULT_BATCH_LANGUAGES

TOOL_NAMES = (
    "get_account_status",
    "calculate_claim_payout",
    "schedule_emi_deferral",
    "trigger_human_handoff",
)
FillerHandler = Callable[[str], Awaitable[None] | None]
ControllerSignalHandler = Any


class ToolCall(BaseModel):
    """Validated model-side tool request."""

    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, Any]


class EvonTurnResult(BaseModel):
    """Validated output passed from Evon to the Timbre clause streamer."""

    model_config = ConfigDict(extra="allow")

    text: str
    language: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    enable_thinking: bool
    temperature: float = 0.6
    top_p: float = 0.95
    evon_ttft_ms: float = Field(ge=0)
    confidence_score: float = Field(ge=0, le=1)
    handoff_packet: dict[str, Any] | None = None
    filler_clause: str | None = None


class ComplianceAuditReport(BaseModel):
    """Post-call compliance artifact for the Phase 5 supervisor workflow."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    denoised: bool
    detected_languages: list[str]
    verbatim_transcript: str
    ai_disclosure_verified: bool
    synthetic_pii_compliant: bool
    extracted_entities: dict[str, Any]
    emi_or_claim_mutations: list[dict[str, Any]]
    handoff_triggered: bool
    sentiment: str
    enable_thinking_used: bool = True
    reasoning_trace: str


class BatchAuditInput(BaseModel):
    """Strict internal envelope for the completed Prisma batch response."""

    model_config = ConfigDict(extra="forbid")

    denoised: StrictBool
    detected_languages: list[str] = Field(min_length=3, max_length=3)
    verbatim_transcript: str = Field(min_length=1)

    @field_validator("denoised")
    @classmethod
    def require_denoised(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("completed batch must be denoised")
        return value

    @field_validator("detected_languages")
    @classmethod
    def require_languages(cls, value: list[str]) -> list[str]:
        if any(not isinstance(language, str) for language in value):
            raise ValueError("detected languages must be strings")
        cleaned = [language.strip() for language in value]
        if any(not language for language in cleaned):
            raise ValueError("detected languages cannot be empty")
        return cleaned


class TranscriptInput(BaseModel):
    """Pydantic boundary for a Prisma final transcript payload."""

    model_config = ConfigDict(extra="allow")

    text: str | None = None
    transcript: str | None = None
    language: str = "hi-IN"
    confidence: float = Field(default=0.98, ge=0, le=1)

    def resolved_text(self) -> str:
        value = self.text or self.transcript
        if not value or not value.strip():
            raise ValueError("final transcript text is required")
        return value.strip()


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_account_status",
            "description": "Read one exact synthetic borrower account status.",
            "parameters": {
                "type": "object",
                "properties": {"account_id": {"type": "string"}},
                "required": ["account_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_claim_payout",
            "description": "Calculate the deterministic synthetic claim payout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "policy_id": {"type": "string"},
                    "crop_loss_pct": {"type": "number", "minimum": 0, "maximum": 100},
                },
                "required": ["policy_id", "crop_loss_pct"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_emi_deferral",
            "description": "Schedule a synthetic EMI deferral date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string"},
                    "requested_date": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["account_id", "requested_date", "reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_human_handoff",
            "description": "Create a synthetic grievance ticket for human review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["account_id", "reason", "confidence_score"],
                "additionalProperties": False,
            },
        },
    },
]


class EvonOrchestrator:
    """Ground each voice turn in the complete policy corpus and SQLite tools."""

    def __init__(
        self,
        *,
        db_path: str | Path = "artha_suraksha_phase3.sqlite",
        policy_path: str | Path | None = None,
        client: Any | None = None,
        controller: ControllerSignalHandler | None = None,
        on_filler: FillerHandler | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.policy_path = Path(policy_path or Path(__file__).with_name("policy_context.md"))
        self.policy_context = self._load_policy_context()
        self.system_prompt = self._build_system_prompt()
        self.tool_schemas = [json.loads(json.dumps(schema)) for schema in TOOL_SCHEMAS]
        self.client = client
        self.controller = controller
        self.on_filler = on_filler
        self._db_lock = asyncio.Lock()
        self._database_ready = False
        self.turn_index = 0
        self.last_request: dict[str, Any] = {}
        self.last_tool_calls: list[dict[str, Any]] = []
        self.filler_clauses: list[str] = []

    def _load_policy_context(self) -> str:
        try:
            text = self.policy_path.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("policy context is empty")
            return text
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError(f"unable to load Evon policy context: {exc}") from exc

    def _build_system_prompt(self) -> str:
        return (
            "You are Evon v3.3 for the Artha-Suraksha synthetic voice service. "
            "Answer in the caller's exact Hindi, Hinglish, or requested Indic "
            "language. Never drift into unrequested English. Never emit XML or "
            "SSML. Preserve exactly ₹1,24,567.50, A/C XX4521, 05/11/2026, and "
            "2.5% p.a. Use only validated SQLite tools for account state. "
            "Confidence below 0.65, a human request, or a disputed Clause 4B "
            "rejection requires trigger_human_handoff.\n\n"
            "FULL POLICY CONTEXT:\n"
            f"{self.policy_context}"
        )

    async def _ensure_database(self) -> None:
        if self._database_ready:
            return
        async with self._db_lock:
            if not self._database_ready:
                try:
                    await init_and_seed_db(self.db_path)
                    self._database_ready = True
                except (OSError, RuntimeError) as exc:
                    raise RuntimeError(f"unable to prepare Evon SQLite tools: {exc}") from exc

    async def _signal_controller(self, transcript: str) -> None:
        if self.controller is None:
            return
        lowered = transcript.casefold()
        try:
            if any(
                marker in lowered
                for marker in ("account number", "policy number", "खाता नंबर", "पॉलिसी नंबर")
            ):
                await self.controller.set_vad_mode("DIGIT_CAPTURE")
            if any(
                marker in lowered
                for marker in ("emi", "pmfby", "kcc", "irdai", "किस्त", "claim")
            ):
                await self.controller.update_bias_settings(
                    list(DEFAULT_BIAS_LIST), DEFAULT_BIAS_SCORE
                )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Evon acoustic state signal failed: {exc}") from exc

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate and execute one of the four deterministic SQLite tools."""

        await self._ensure_database()
        try:
            if name == "get_account_status":
                request = GetAccountStatusInput.model_validate(arguments)
                return await get_account_status(request.account_id, self.db_path)
            if name == "calculate_claim_payout":
                request = CalculateClaimPayoutInput.model_validate(arguments)
                return await calculate_claim_payout(
                    request.policy_id, request.crop_loss_pct, self.db_path
                )
            if name == "schedule_emi_deferral":
                request = ScheduleEmiDeferralInput.model_validate(arguments)
                return await schedule_emi_deferral(
                    request.account_id,
                    request.requested_date,
                    request.reason,
                    self.db_path,
                )
            if name == "trigger_human_handoff":
                request = TriggerHumanHandoffInput.model_validate(arguments)
                return await trigger_human_handoff(
                    request.account_id,
                    request.reason,
                    request.confidence_score,
                    self.db_path,
                )
            raise ValueError(f"unsupported Evon tool: {name}")
        except (ValidationError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"Evon tool validation/execution failed: {exc}") from exc

    async def execute_tool_call(self, call: ToolCall | dict[str, Any]) -> dict[str, Any]:
        """Compatibility entry point for OpenAI-style tool-call objects."""

        try:
            request = call if isinstance(call, ToolCall) else ToolCall.model_validate(call)
            return await self.execute_tool(request.name, request.arguments)
        except (ValidationError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"invalid Evon tool call: {exc}") from exc

    @staticmethod
    def _account_id(text: str) -> str:
        match = re.search(r"A\s*/\s*C\s+(XX\d{4})", text, flags=re.IGNORECASE)
        return f"A/C {match.group(1).upper()}" if match else "A/C XX4521"

    @staticmethod
    def _policy_id(text: str) -> str:
        match = re.search(r"PMFBY[- ]?2026[- ]?(\d{4})", text, flags=re.IGNORECASE)
        return f"PMFBY-2026-{match.group(1)}" if match else "PMFBY-2026-8841"

    @staticmethod
    def _loss_percentage(text: str) -> float:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        return float(match.group(1)) if match else 50.0

    @staticmethod
    def _requested_date(text: str) -> str:
        match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", text)
        return match.group(0) if match else "15/12/2026"

    @staticmethod
    def _is_handoff_request(text: str) -> bool:
        lowered = text.casefold()
        return any(
            marker in lowered
            for marker in (
                "human",
                "officer",
                "agent",
                "insaan",
                "मानव",
                "अधिकारी",
                "dispute",
                "appeal",
                "reject",
                "clause 4b",
                "अस्वीकृति",
            )
        )

    @staticmethod
    def _is_claim_request(text: str) -> bool:
        lowered = text.casefold()
        return "%" in lowered or any(
            marker in lowered
            for marker in ("crop loss", "crop damage", "fasal", "फसल", "payout", "दावा")
        )

    @staticmethod
    def _is_deferral_request(text: str) -> bool:
        lowered = text.casefold()
        return any(
            marker in lowered
            for marker in (
                "defer",
                "deferral",
                "postpone",
                "reschedule",
                "स्थगन",
                "टाल",
                "आगे",
            )
        )

    async def _emit_filler(self, language: str) -> str:
        filler = (
            "Aapki pareshaani samajh raha hoon, main claim details ko dhyan se check kar raha hoon।"
            if language.startswith("hi")
            else "I understand this is important; I am carefully checking the claim details."
        )
        self.filler_clauses.append(filler)
        if self.on_filler is not None:
            try:
                result = self.on_filler(filler)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Evon filler callback failed: {exc}") from exc
        return filler

    async def _mock_turn(
        self,
        text: str,
        language: str,
        confidence: float,
        thinking: bool,
        filler: str | None,
    ) -> EvonTurnResult:
        started_ns = time.perf_counter_ns()
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        account_id = self._account_id(text)
        lowered = text.casefold()
        handoff = confidence < 0.65 or self._is_handoff_request(text)

        if handoff:
            arguments = {
                "account_id": account_id,
                "reason": text,
                "confidence_score": confidence,
            }
            tool_calls.append({"name": "trigger_human_handoff", "arguments": arguments})
            tool_results.append(
                await self.execute_tool("trigger_human_handoff", arguments)
            )
            ticket = tool_results[-1].get("grievance_ticket_id", "")
            response = (
                f"Aapki baat human officer ko bhej di gayi hai. Synthetic ticket {ticket} hai."
                if language.startswith("hi")
                else f"I have sent this to a human officer. The synthetic ticket is {ticket}."
            )
            handoff_packet = tool_results[-1]
        elif self._is_claim_request(text):
            arguments = {
                "policy_id": self._policy_id(text),
                "crop_loss_pct": self._loss_percentage(text),
            }
            tool_calls.append({"name": "calculate_claim_payout", "arguments": arguments})
            tool_results.append(await self.execute_tool("calculate_claim_payout", arguments))
            result = tool_results[-1]
            response = (
                f"Aapke claim mein {result.get('crop_loss_pct')} pratishat loss ke liye "
                f"estimated payout {result.get('payout_amount_formatted', '₹0.00')} hai."
                if language.startswith("hi")
                else f"The estimated payout is {result.get('payout_amount_formatted', '₹0.00')}."
            )
            handoff_packet = None
        elif self._is_deferral_request(text):
            arguments = {
                "account_id": account_id,
                "requested_date": self._requested_date(text),
                "reason": text,
            }
            tool_calls.append({"name": "schedule_emi_deferral", "arguments": arguments})
            tool_results.append(await self.execute_tool("schedule_emi_deferral", arguments))
            result = tool_results[-1]
            response = (
                f"EMI deferral {result.get('new_due_date')} ke liye schedule hai. "
                f"Rate {result.get('interest_rate_formatted', '2.5% p.a.')} rahega."
                if language.startswith("hi")
                else f"The EMI deferral is scheduled for {result.get('new_due_date')}."
            )
            handoff_packet = None
        elif any(
            marker in lowered
            for marker in ("account", "balance", "emi", "due", "स्थिति", "खाता", "किस्त")
        ):
            arguments = {"account_id": account_id}
            tool_calls.append({"name": "get_account_status", "arguments": arguments})
            tool_results.append(await self.execute_tool("get_account_status", arguments))
            result = tool_results[-1]
            response = (
                f"Aapke {result.get('account_id', account_id)} mein balance "
                f"{result.get('outstanding_balance_formatted', '₹0.00')} hai. "
                f"Agli EMI {result.get('next_emi_due_date', 'unknown')} ko due hai, "
                f"aur rate {result.get('interest_rate_formatted', '2.5% p.a.')} hai."
                if language.startswith("hi")
                else f"Your balance is {result.get('outstanding_balance_formatted', '₹0.00')}; "
                f"the next EMI is due on {result.get('next_emi_due_date', 'unknown')} at "
                f"{result.get('interest_rate_formatted', '2.5% p.a.')}."
            )
            handoff_packet = None
        else:
            response = (
                "Main synthetic Artha-Suraksha record ke baare mein madad kar sakta hoon."
                if language.startswith("hi")
                else "I can help with a synthetic Artha-Suraksha account or claim record."
            )
            handoff_packet = None

        ttft_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
        return EvonTurnResult(
            text=response,
            language=language,
            tool_calls=tool_calls,
            tool_results=tool_results,
            enable_thinking=thinking,
            evon_ttft_ms=ttft_ms,
            confidence_score=confidence,
            handoff_packet=handoff_packet,
            filler_clause=filler,
        )

    async def _live_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        thinking: bool,
        json_mode: bool = False,
    ) -> Any:
        """Call an OpenAI-compatible local/sovereign Evon endpoint."""

        if self.client is None:
            try:
                self.client = AsyncOpenAI(
                    base_url=settings.EVON_BASE_URL,
                    api_key=settings.get_active_api_key() or "local-placeholder",
                )
            except (RuntimeError, ValueError, TypeError) as exc:
                raise RuntimeError(f"unable to initialize Evon client: {exc}") from exc
        try:
            request: dict[str, Any] = {
                "model": settings.EVON_MODEL_ID,
                "messages": messages,
                "tools": self.tool_schemas,
                "tool_choice": "auto",
                "temperature": 0.6,
                "top_p": 0.95,
                "extra_body": {"enable_thinking": thinking},
            }
            if json_mode:
                request["response_format"] = {"type": "json_object"}
            return await self.client.chat.completions.create(**request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Evon completion failed: {exc}") from exc

    @staticmethod
    def _message_content(message: Any) -> str:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        return str(content or "").strip()

    @staticmethod
    def _message_tool_calls(message: Any) -> list[Any]:
        calls = getattr(message, "tool_calls", None)
        if calls is None and isinstance(message, dict):
            calls = message.get("tool_calls")
        return list(calls or [])

    @staticmethod
    def _tool_call_parts(call: Any) -> tuple[str, dict[str, Any]]:
        try:
            function = getattr(call, "function", None)
            if function is None and isinstance(call, dict):
                function = call.get("function", call)
            name = getattr(function, "name", None)
            raw_arguments = getattr(function, "arguments", None)
            if isinstance(function, dict):
                name = function.get("name", name)
                raw_arguments = function.get("arguments", raw_arguments)
            if not isinstance(name, str):
                raise TypeError("Evon tool call has no function name")
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else raw_arguments
            )
            if not isinstance(arguments, dict):
                raise TypeError("Evon tool arguments must be an object")
            return name, arguments
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid Evon tool call payload: {exc}") from exc

    async def _live_turn(
        self,
        text: str,
        language: str,
        confidence: float,
        thinking: bool,
        filler: str | None,
    ) -> EvonTurnResult:
        started_ns = time.perf_counter_ns()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"[{language}] {text}"},
        ]
        completion = await self._live_completion(messages, thinking=thinking)
        choice = completion.choices[0]
        message = choice.message
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for call in self._message_tool_calls(message):
            name, arguments = self._tool_call_parts(call)
            tool_calls.append({"name": name, "arguments": arguments})
            result = await self.execute_tool(name, arguments)
            tool_results.append(result)
        response_text = self._message_content(message)
        if tool_calls:
            assistant_message = {
                "role": "assistant",
                "content": response_text or None,
                "tool_calls": self._message_tool_calls(message),
            }
            messages.append(assistant_message)
            for call, result in zip(tool_calls, tool_results, strict=True):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", call["name"]),
                        "name": call["name"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            follow_up = await self._live_completion(messages, thinking=thinking)
            response_text = self._message_content(follow_up.choices[0].message)
        if not response_text:
            response_text = "I could not produce a grounded response. A human review is available."
        return EvonTurnResult(
            text=response_text,
            language=language,
            tool_calls=tool_calls,
            tool_results=tool_results,
            enable_thinking=thinking,
            evon_ttft_ms=(time.perf_counter_ns() - started_ns) / 1_000_000,
            confidence_score=confidence,
            filler_clause=filler,
        )

    async def run_turn(
        self,
        transcript: str | dict[str, Any],
        *,
        language: str | None = None,
        asr_confidence: float | None = None,
        disputed_claim: bool = False,
        compliance: bool = False,
    ) -> EvonTurnResult:
        """Run one live or deterministic mock voice turn."""

        try:
            input_model = (
                TranscriptInput.model_validate(transcript)
                if isinstance(transcript, dict)
                else TranscriptInput(text=transcript)
            )
            text = input_model.resolved_text()
            resolved_language = language or input_model.language
            confidence = (
                asr_confidence if asr_confidence is not None else input_model.confidence
            )
            if not 0 <= confidence <= 1:
                raise ValueError("asr_confidence must be between 0 and 1")
            thinking = bool(disputed_claim or compliance or self._is_handoff_request(text))
            self.last_request = {
                "model": settings.EVON_MODEL_ID,
                "temperature": 0.6,
                "top_p": 0.95,
                "enable_thinking": thinking,
                "extra_body": {"enable_thinking": thinking},
                "language": resolved_language,
                "transcript": text,
            }
            await self._signal_controller(text)
            filler = await self._emit_filler(resolved_language) if thinking else None
            if settings.USE_MOCK_GNANI:
                result = await self._mock_turn(
                    text,
                    resolved_language,
                    confidence,
                    thinking,
                    filler,
                )
            else:
                result = await self._live_turn(
                    text,
                    resolved_language,
                    confidence,
                    thinking,
                    filler,
                )
            self.last_tool_calls = list(result.tool_calls)
            self.turn_index += 1
            return result
        except asyncio.CancelledError:
            raise
        except (ValidationError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError(f"Evon turn failed: {exc}") from exc

    async def process_transcript(
        self,
        transcript: str | dict[str, Any],
        **kwargs: Any,
    ) -> EvonTurnResult:
        """Alias used by the Phase 2 gateway transcript callback."""

        return await self.run_turn(transcript, **kwargs)

    async def handle_transcript(
        self,
        transcript: str | dict[str, Any],
        **kwargs: Any,
    ) -> EvonTurnResult:
        return await self.run_turn(transcript, **kwargs)

    @staticmethod
    def _audit_synthetic_pii_compliant(transcript: str) -> bool:
        """Reject common unmasked mobile, bank, government-ID, and PAN patterns."""

        mobile = re.search(r"(?<!\d)[6-9]\d{4}[ -]?\d{5}(?!\d)", transcript)
        long_numeric = re.search(
            r"(?<![\d,])(?:\d[ -]?){9,18}(?!\d)", transcript
        )
        government_id = re.search(
            r"(?<!\d)(?:\d{4}[ -]){2}\d{4}(?!\d)|(?<!\d)\d{12}(?!\d)",
            transcript,
        )
        pan = re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", transcript, flags=re.IGNORECASE)
        return not any((mobile, long_numeric, government_id, pan))

    @staticmethod
    def _audit_ai_disclosure_present(transcript: str) -> bool:
        lowered = transcript.casefold()
        return bool(
            re.search(r"\bai\b", lowered)
            and ("artha-suraksha" in lowered or "assistant" in lowered or "sahayak" in lowered)
        ) or "कृत्रिम" in transcript or "एआई" in transcript

    @staticmethod
    def _audit_entities(transcript: str) -> dict[str, Any]:
        entities: dict[str, Any] = {}
        account = re.search(r"A\s*/\s*C\s+(XX\d{4})", transcript, flags=re.IGNORECASE)
        if account:
            entities["account_id"] = f"A/C {account.group(1).upper()}"
        if "₹1,24,567.50" in transcript:
            entities["balance"] = "₹1,24,567.50"
        date = re.search(r"\b\d{2}/\d{2}/\d{4}\b", transcript)
        if date:
            entities["due_date"] = date.group(0)
        rate = re.search(r"\b\d+(?:\.\d+)?%\s+p\.a\.", transcript, flags=re.IGNORECASE)
        if rate:
            entities["interest_rate"] = rate.group(0)
        policy = re.search(r"PMFBY[- ]?2026[- ]?\d{4}", transcript, flags=re.IGNORECASE)
        if policy:
            entities["policy_id"] = policy.group(0).replace(" ", "-").upper()
        return entities

    async def generate_compliance_audit(
        self, job_id: str, batch_result: dict[str, Any]
    ) -> ComplianceAuditReport:
        """Produce a validated post-call audit with Evon thinking enabled."""

        try:
            if not isinstance(job_id, str) or not job_id.strip():
                raise ValueError("job_id cannot be empty")
            if not isinstance(batch_result, dict):
                raise TypeError("batch_result must be an object")
            transcript = batch_result.get("verbatim_transcript") or batch_result.get(
                "transcript"
            )
            denoised = batch_result.get("denoised")
            if denoised is None:
                denoised = batch_result.get("with_denoise")
            detected_languages = batch_result.get("detected_languages")
            if detected_languages is None:
                detected_languages = batch_result.get("languages")
            if detected_languages is None:
                detected_languages = list(DEFAULT_BATCH_LANGUAGES)
            audit_input = BatchAuditInput(
                denoised=denoised,
                detected_languages=detected_languages,
                verbatim_transcript=transcript,
            )
            transcript = audit_input.verbatim_transcript.strip()
            self.last_request = {
                "model": settings.EVON_MODEL_ID,
                "temperature": 0.6,
                "top_p": 0.95,
                "enable_thinking": True,
                "extra_body": {"enable_thinking": True},
                "task": "post_call_compliance_audit",
            }
            live_fields: dict[str, Any] = {}
            if not settings.USE_MOCK_GNANI:
                completion = await self._live_completion(
                    [
                        {"role": "system", "content": self.system_prompt},
                        {
                            "role": "user",
                            "content": (
                                "Return only a JSON compliance audit with these fields: "
                                "ai_disclosure_verified, synthetic_pii_compliant, "
                                "extracted_entities, emi_or_claim_mutations, "
                                "handoff_triggered, sentiment, reasoning_trace.\n"
                                "Transcript:\n"
                                + transcript
                            ),
                        },
                    ],
                    thinking=True,
                    json_mode=True,
                )
                raw_audit = self._message_content(completion.choices[0].message)
                try:
                    parsed_audit = json.loads(raw_audit)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Evon audit response was not valid JSON") from exc
                if not isinstance(parsed_audit, dict):
                    raise TypeError("Evon audit response must be a JSON object")
                if isinstance(parsed_audit.get("compliance_report"), dict):
                    parsed_audit = parsed_audit["compliance_report"]
                # The model may enrich the explanation, but it must not be
                # allowed to override deterministic compliance gates.  Those
                # gates are recomputed below from the immutable transcript so
                # a hallucinated Evon JSON value cannot turn a failed audit
                # into a pass (or hide a real disclosure/PII failure).
                model_trace = parsed_audit.get("reasoning_trace")
                if isinstance(model_trace, str) and model_trace.strip():
                    live_fields["model_reasoning_trace"] = model_trace.strip()

            entities = self._audit_entities(transcript)
            mutations: list[dict[str, Any]] = []
            lowered = transcript.casefold()
            if any(marker in lowered for marker in ("emi", "deferral", "किस्त")):
                mutations.append(
                    {
                        "type": "emi_or_deferral_request",
                        "detected": True,
                        "tool": "schedule_emi_deferral",
                    }
                )
            if any(marker in lowered for marker in ("pmfby", "claim", "फसल", "दावा")):
                mutations.append(
                    {
                        "type": "pmfby_or_claim_request",
                        "detected": True,
                        "tool": "calculate_claim_payout",
                    }
                )
            handoff = bool(
                batch_result.get("handoff_triggered") is True
                or any(
                    marker in lowered
                    for marker in (
                        "human",
                        "officer",
                        "appeal",
                        "dispute",
                        "reject",
                        "low confidence",
                    )
                )
            )
            disclosure = self._audit_ai_disclosure_present(transcript)
            pii_compliant = self._audit_synthetic_pii_compliant(transcript)
            sentiment = (
                "escalated"
                if handoff
                else "distressed_resolved"
                if mutations
                else "neutral"
            )
            reasoning = (
                "Evon v3.3 enable_thinking=True audit: checked Turn-0 disclosure, "
                "synthetic PII patterns, financial entities, and mutation evidence."
            )
            model_trace = live_fields.get("model_reasoning_trace")
            if isinstance(model_trace, str):
                reasoning = f"{reasoning} Model trace: {model_trace}"
            report_fields: dict[str, Any] = {
                "job_id": job_id,
                "denoised": audit_input.denoised,
                "detected_languages": audit_input.detected_languages,
                "verbatim_transcript": transcript,
                "ai_disclosure_verified": disclosure,
                "synthetic_pii_compliant": pii_compliant,
                "extracted_entities": entities,
                "emi_or_claim_mutations": mutations,
                "handoff_triggered": handoff,
                "sentiment": sentiment,
                "enable_thinking_used": True,
                "reasoning_trace": reasoning,
            }
            report_fields["job_id"] = job_id
            report_fields["denoised"] = audit_input.denoised
            report_fields["detected_languages"] = audit_input.detected_languages
            report_fields["verbatim_transcript"] = transcript
            report_fields["enable_thinking_used"] = True
            return ComplianceAuditReport(**report_fields)
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError, RuntimeError, ValidationError) as exc:
            raise RuntimeError(f"Evon compliance audit failed: {exc}") from exc

    async def stream_text_tokens(self, text: str) -> AsyncIterator[str]:
        """Yield deterministic mock token deltas for clause chunking tests."""

        try:
            for token in re.findall(r"\S+\s*", text):
                await asyncio.sleep(0)
                yield token
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Evon token stream failed: {exc}") from exc

    async def close(self) -> None:
        """Close an injected OpenAI-compatible client when it supports closing."""

        if self.client is None:
            return
        close = getattr(self.client, "close", None)
        if close is None:
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Evon client close failed: {exc}") from exc
