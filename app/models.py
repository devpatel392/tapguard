"""Core data types shared across TapGuard."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, computed_field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IntentKind(str, Enum):
    TRANSFER = "transfer"  # move tokens to an address
    APPROVE = "approve"    # let an address/contract spend your tokens (an allowance)


class TxIntent(BaseModel):
    """What the agent *wants* to do. Nothing is signed at this stage."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    kind: IntentKind = IntentKind.TRANSFER
    to: str                      # recipient (transfer) or spender (approve)
    amount: float | None = None  # None = unlimited (approvals only)
    token: str
    chain: str
    memo: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    DANGER = "danger"


class RiskFlag(BaseModel):
    code: str
    severity: Severity
    message: str


class Verdict(str, Enum):
    ALLOW = "allow"    # queued; owner taps the card to sign
    REVIEW = "review"  # queued; owner must acknowledge warnings, then tap
    BLOCK = "block"    # never reaches the card


class PolicyDecision(BaseModel):
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    flags: list[RiskFlag] = Field(default_factory=list)
    usd_value: float | None = None


class ApprovalStatus(str, Enum):
    PENDING = "pending"    # waiting for a tap
    SIGNED = "signed"
    REJECTED = "rejected"  # owner said no
    BLOCKED = "blocked"    # policy said no
    FAILED = "failed"      # wallet/MCP error while signing


class Approval(BaseModel):
    """One proposal and everything we decided about it."""

    intent: TxIntent
    decision: PolicyDecision
    explanation: str
    recipient_label: str | None = None
    user_text: str | None = None  # what the owner typed when this was proposed
    status: ApprovalStatus = ApprovalStatus.PENDING
    tx_ref: str | None = None
    error: str | None = None
    resolved_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        return self.intent.id


class Balance(BaseModel):
    token: str
    chain: str
    amount: float


class PastTx(BaseModel):
    to: str                      # recipient ("self" for incoming)
    amount: float
    token: str
    chain: str
    ref: str = ""
    timestamp: datetime | None = None
    direction: str = "out"       # "out" = you paid; "in" = someone sent to you (anyone can, incl. scammers)
    from_address: str = ""       # sender, for incoming
    memo: str = ""               # attacker-controlled for incoming txs: treat as untrusted text


class Contact(BaseModel):
    name: str
    address: str


class AuditEvent(BaseModel):
    at: datetime = Field(default_factory=utcnow)
    kind: str
    message: str
