"""Validated, deterministic SQLite tools for the Phase 1 foundation.

The database is intentionally small and synthetic.  It is a local tool
contract for later orchestration work, not a connection to a banking or
insurance system.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DB_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS borrowers (
    account_id TEXT PRIMARY KEY,
    policy_id TEXT,
    holder_name TEXT,
    preferred_language TEXT,
    outstanding_balance_inr REAL,
    next_emi_due_date TEXT,
    interest_rate_pa REAL,
    claim_status TEXT,
    grievance_ticket_id TEXT NULL
)
"""

MANDATORY_ACCOUNT_ID: Final[str] = "A/C XX4521"
MANDATORY_POLICY_ID: Final[str] = "PMFBY-2026-8841"
CLAIM_THRESHOLD_PERCENT: Final[float] = 33.0


class StrictModel(BaseModel):
    """Common strictness for every persisted or tool-bound model."""

    model_config = ConfigDict(extra="forbid")


class BorrowerRecord(StrictModel):
    """The exact row shape stored in the synthetic borrower table."""

    account_id: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    holder_name: str = Field(min_length=1)
    preferred_language: str = Field(min_length=2)
    outstanding_balance_inr: float = Field(ge=0)
    next_emi_due_date: str = Field(min_length=10)
    interest_rate_pa: float = Field(ge=0)
    claim_status: str = Field(min_length=1)
    grievance_ticket_id: str | None = None


class AccountStatusInput(StrictModel):
    account_id: str = Field(min_length=1)


GetAccountStatusInput = AccountStatusInput


class ClaimPayoutInput(StrictModel):
    policy_id: str = Field(min_length=1)
    crop_loss_pct: float = Field(ge=0, le=100)

    @field_validator("crop_loss_pct")
    @classmethod
    def require_finite_loss(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("crop_loss_pct must be finite")
        return value


CalculateClaimPayoutInput = ClaimPayoutInput


class EmiDeferralInput(StrictModel):
    account_id: str = Field(min_length=1)
    requested_date: str = Field(min_length=10)
    reason: str = Field(min_length=3)

    @field_validator("requested_date")
    @classmethod
    def require_indian_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%d/%m/%Y")
        except ValueError as exc:
            raise ValueError("requested_date must use DD/MM/YYYY") from exc
        return value


ScheduleEmiDeferralInput = EmiDeferralInput


class HumanHandoffInput(StrictModel):
    account_id: str = Field(min_length=1)
    reason: str = Field(min_length=3)
    confidence_score: float = Field(ge=0, le=1)

    @field_validator("confidence_score")
    @classmethod
    def require_finite_confidence(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("confidence_score must be finite")
        return value


TriggerHumanHandoffInput = HumanHandoffInput


class ToolError(StrictModel):
    success: Literal[False] = False
    status: Literal["error"] = "error"
    error: str


class AccountStatusOutput(StrictModel):
    success: Literal[True] = True
    status: Literal["found"] = "found"
    account_id: str
    policy_id: str
    holder_name: str
    preferred_language: str
    outstanding_balance_inr: float
    outstanding_balance_formatted: str
    outstanding_balance: str
    next_emi_due_date: str
    interest_rate_pa: float
    interest_rate_formatted: str
    interest_rate: str
    claim_status: str
    grievance_ticket_id: str | None


class ClaimPayoutOutput(StrictModel):
    success: Literal[True] = True
    status: Literal["calculated"] = "calculated"
    policy_id: str
    account_id: str
    crop_loss_pct: float
    eligible: bool
    payout_amount_inr: float
    payout_amount_formatted: str
    payout_amount: str
    formula: str


class EmiDeferralOutput(StrictModel):
    success: Literal[True] = True
    status: Literal["scheduled"] = "scheduled"
    account_id: str
    previous_due_date: str
    requested_date: str
    new_due_date: str
    reason: str
    interest_rate_formatted: str
    nach_mandate_action: str


class HumanHandoffOutput(StrictModel):
    success: Literal[True] = True
    status: Literal["handoff_requested"] = "handoff_requested"
    account_id: str
    grievance_ticket_id: str
    reason: str
    confidence_score: float
    mandatory_escalation: bool
    escalation_reason: str


def format_inr(amount: float) -> str:
    """Format a rupee amount using Indian grouping and two decimals."""

    value = Decimal(str(amount)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    absolute = f"{abs(value):.2f}"
    whole, fraction = absolute.split(".")
    if len(whole) > 3:
        last_three = whole[-3:]
        prefix = whole[:-3]
        groups: list[str] = []
        while len(prefix) > 2:
            groups.insert(0, prefix[-2:])
            prefix = prefix[:-2]
        if prefix:
            groups.insert(0, prefix)
        whole = ",".join([*groups, last_three])
    return f"{sign}₹{whole}.{fraction}"


def format_interest_rate(rate: float) -> str:
    """Format the persisted annual rate without introducing SSML."""

    return f"{rate:.1f}% p.a."


def _database_path(db_path: str | Path) -> str:
    if isinstance(db_path, Path):
        return str(db_path)
    return db_path


def _synthetic_records() -> list[BorrowerRecord]:
    """Create the fixed, reproducible set of 50 entirely fictitious rows."""

    records = [
        BorrowerRecord(
            account_id=MANDATORY_ACCOUNT_ID,
            policy_id=MANDATORY_POLICY_ID,
            holder_name="Fictitious Holder 01",
            preferred_language="hi-IN",
            outstanding_balance_inr=124567.50,
            next_emi_due_date="05/11/2026",
            interest_rate_pa=2.5,
            claim_status="UNDER_REVIEW",
            grievance_ticket_id=None,
        )
    ]
    languages = ("hi-IN", "en-IN", "mr-IN", "bn-IN")
    claim_statuses = ("UNDER_REVIEW", "APPROVED", "PENDING_DOCUMENTS", "REJECTED")
    due_dates = ("05/11/2026", "05/12/2026", "05/01/2027", "05/02/2027")
    for sequence in range(2, 51):
        records.append(
            BorrowerRecord(
                account_id=f"A/C XX{sequence:04d}",
                policy_id=f"PMFBY-2026-{8840 + sequence:04d}",
                holder_name=f"Fictitious Holder {sequence:02d}",
                preferred_language=languages[(sequence - 2) % len(languages)],
                outstanding_balance_inr=round(18000 + sequence * 1375.25, 2),
                next_emi_due_date=due_dates[(sequence - 2) % len(due_dates)],
                interest_rate_pa=2.5,
                claim_status=claim_statuses[(sequence - 2) % len(claim_statuses)],
                grievance_ticket_id=None,
            )
        )
    return records


async def init_and_seed_db(db_path: str | Path) -> int:
    """Create the schema and replace its contents with 50 synthetic rows."""

    path = _database_path(db_path)
    try:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        records = _synthetic_records()
        async with aiosqlite.connect(path) as db:
            await db.execute(DB_SCHEMA)
            await db.execute("DELETE FROM borrowers")
            await db.executemany(
                """
                INSERT INTO borrowers (
                    account_id, policy_id, holder_name, preferred_language,
                    outstanding_balance_inr, next_emi_due_date, interest_rate_pa,
                    claim_status, grievance_ticket_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.account_id,
                        record.policy_id,
                        record.holder_name,
                        record.preferred_language,
                        record.outstanding_balance_inr,
                        record.next_emi_due_date,
                        record.interest_rate_pa,
                        record.claim_status,
                        record.grievance_ticket_id,
                    )
                    for record in records
                ],
            )
            await db.commit()
            async with db.execute("SELECT COUNT(*) FROM borrowers") as cursor:
                row = await cursor.fetchone()
        count = int(row[0]) if row else 0
        if count != 50:
            raise RuntimeError(f"synthetic seed count mismatch: expected 50, got {count}")
        return count
    except (OSError, aiosqlite.Error, RuntimeError) as exc:
        raise RuntimeError(f"unable to initialize synthetic database: {exc}") from exc


async def _fetch_borrower(db: aiosqlite.Connection, lookup: str, value: str) -> BorrowerRecord | None:
    """Fetch and validate one row while keeping SQLite errors explicit."""

    try:
        query = (
            "SELECT account_id, policy_id, holder_name, preferred_language, "
            "outstanding_balance_inr, next_emi_due_date, interest_rate_pa, "
            f"claim_status, grievance_ticket_id FROM borrowers WHERE {lookup} = ?"
        )
        async with db.execute(
            query,
            (value,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return BorrowerRecord.model_validate(dict(row))
    except (aiosqlite.Error, ValidationError, TypeError, ValueError) as exc:
        raise RuntimeError(f"unable to read borrower record: {exc}") from exc


def _error(exc: Exception) -> dict[str, Any]:
    """Return a validated, non-secret error object from a tool boundary."""

    return ToolError(error=str(exc)).model_dump()


async def get_account_status(
    account_id: str, db_path: str | Path
) -> dict[str, Any]:
    """Return a formatted, validated status snapshot for one account."""

    try:
        request = AccountStatusInput(account_id=account_id)
        async with aiosqlite.connect(_database_path(db_path)) as db:
            db.row_factory = aiosqlite.Row
            borrower = await _fetch_borrower(db, "account_id", request.account_id)
        if borrower is None:
            return _error(ValueError(f"account not found: {request.account_id}"))
        result = AccountStatusOutput(
            account_id=borrower.account_id,
            policy_id=borrower.policy_id,
            holder_name=borrower.holder_name,
            preferred_language=borrower.preferred_language,
            outstanding_balance_inr=borrower.outstanding_balance_inr,
            outstanding_balance_formatted=format_inr(
                borrower.outstanding_balance_inr
            ),
            outstanding_balance=format_inr(borrower.outstanding_balance_inr),
            next_emi_due_date=borrower.next_emi_due_date,
            interest_rate_pa=borrower.interest_rate_pa,
            interest_rate_formatted=format_interest_rate(borrower.interest_rate_pa),
            interest_rate=format_interest_rate(borrower.interest_rate_pa),
            claim_status=borrower.claim_status,
            grievance_ticket_id=borrower.grievance_ticket_id,
        )
        return result.model_dump()
    except (ValidationError, OSError, aiosqlite.Error, RuntimeError, ValueError) as exc:
        return _error(exc)


async def calculate_claim_payout(
    policy_id: str, crop_loss_pct: float, db_path: str | Path
) -> dict[str, Any]:
    """Calculate the synthetic PMFBY payout using a transparent threshold rule."""

    try:
        request = ClaimPayoutInput(
            policy_id=policy_id,
            crop_loss_pct=crop_loss_pct,
        )
        async with aiosqlite.connect(_database_path(db_path)) as db:
            db.row_factory = aiosqlite.Row
            borrower = await _fetch_borrower(db, "policy_id", request.policy_id)
        if borrower is None:
            return _error(ValueError(f"policy not found: {request.policy_id}"))

        eligible = request.crop_loss_pct >= CLAIM_THRESHOLD_PERCENT
        payout = (
            borrower.outstanding_balance_inr * request.crop_loss_pct / 100
            if eligible
            else 0.0
        )
        payout = round(payout, 2)
        formula = (
            "eligible loss >= 33%: min(outstanding_balance_inr, "
            "outstanding_balance_inr * crop_loss_pct / 100)"
            if eligible
            else "loss below 33% threshold: payout = 0"
        )
        result = ClaimPayoutOutput(
            policy_id=borrower.policy_id,
            account_id=borrower.account_id,
            crop_loss_pct=request.crop_loss_pct,
            eligible=eligible,
            payout_amount_inr=payout,
            payout_amount_formatted=format_inr(payout),
            payout_amount=format_inr(payout),
            formula=formula,
        )
        return result.model_dump()
    except (ValidationError, OSError, aiosqlite.Error, RuntimeError, ValueError) as exc:
        return _error(exc)


async def schedule_emi_deferral(
    account_id: str,
    requested_date: str,
    reason: str,
    db_path: str | Path,
) -> dict[str, Any]:
    """Schedule one synthetic EMI date while preserving its interest rate."""

    try:
        request = EmiDeferralInput(
            account_id=account_id,
            requested_date=requested_date,
            reason=reason,
        )
        async with aiosqlite.connect(_database_path(db_path)) as db:
            db.row_factory = aiosqlite.Row
            borrower = await _fetch_borrower(db, "account_id", request.account_id)
            if borrower is None:
                return _error(ValueError(f"account not found: {request.account_id}"))
            await db.execute(
                "UPDATE borrowers SET next_emi_due_date = ? WHERE account_id = ?",
                (request.requested_date, request.account_id),
            )
            await db.commit()
        result = EmiDeferralOutput(
            account_id=borrower.account_id,
            previous_due_date=borrower.next_emi_due_date,
            requested_date=request.requested_date,
            new_due_date=request.requested_date,
            reason=request.reason,
            interest_rate_formatted=format_interest_rate(borrower.interest_rate_pa),
            nach_mandate_action="PRESERVE_MANDATE_AND_REPRESENT_ON_APPROVED_DATE",
        )
        return result.model_dump()
    except (ValidationError, OSError, aiosqlite.Error, RuntimeError, ValueError) as exc:
        return _error(exc)


def _ticket_id(account_id: str) -> str:
    """Build a deterministic synthetic ticket without storing personal data."""

    safe_suffix = account_id.replace("/", "-").replace(" ", "-")
    return f"HND-{safe_suffix}"


async def trigger_human_handoff(
    account_id: str,
    reason: str,
    confidence_score: float,
    db_path: str | Path,
) -> dict[str, Any]:
    """Create a deterministic grievance ticket and mark the account escalated."""

    try:
        request = HumanHandoffInput(
            account_id=account_id,
            reason=reason,
            confidence_score=confidence_score,
        )
        async with aiosqlite.connect(_database_path(db_path)) as db:
            db.row_factory = aiosqlite.Row
            borrower = await _fetch_borrower(db, "account_id", request.account_id)
            if borrower is None:
                return _error(ValueError(f"account not found: {request.account_id}"))
            ticket_id = borrower.grievance_ticket_id or _ticket_id(request.account_id)
            await db.execute(
                """
                UPDATE borrowers
                SET grievance_ticket_id = ?, claim_status = ?
                WHERE account_id = ?
                """,
                (ticket_id, "ESCALATED_TO_HUMAN", request.account_id),
            )
            await db.commit()

        lowered_reason = request.reason.casefold()
        disputed = any(
            marker in lowered_reason for marker in ("dispute", "appeal", "reject")
        )
        low_confidence = request.confidence_score < 0.65
        escalation_reason = (
            "confidence below 0.65"
            if low_confidence
            else "caller disputes or appeals a rejection"
            if disputed
            else "caller requested human assistance"
        )
        result = HumanHandoffOutput(
            account_id=request.account_id,
            grievance_ticket_id=ticket_id,
            reason=request.reason,
            confidence_score=request.confidence_score,
            mandatory_escalation=low_confidence or disputed,
            escalation_reason=escalation_reason,
        )
        return result.model_dump()
    except (ValidationError, OSError, aiosqlite.Error, RuntimeError, ValueError) as exc:
        return _error(exc)
