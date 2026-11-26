"""In-memory state. Fine for a demo; swap for SQLite/Postgres if you keep building."""
from __future__ import annotations

from .models import Approval, ApprovalStatus, AuditEvent, IntentKind, utcnow


class Store:
    def __init__(self, max_audit: int = 200):
        self.approvals: dict[str, Approval] = {}
        self.audit: list[AuditEvent] = []
        self._max_audit = max_audit

    def add(self, approval: Approval) -> None:
        self.approvals[approval.id] = approval

    def get(self, approval_id: str) -> Approval | None:
        return self.approvals.get(approval_id)

    def all(self) -> list[Approval]:
        return sorted(self.approvals.values(), key=lambda a: a.intent.created_at, reverse=True)

    def log(self, kind: str, message: str) -> None:
        self.audit.append(AuditEvent(kind=kind, message=message))
        del self.audit[: -self._max_audit]

    def spent_today_usd(self, exclude_id: str | None = None, to: str | None = None) -> float:
        """Signed + pending payments today (UTC). Pending counts so the cap can't be dodged."""
        today = utcnow().date()
        total = 0.0
        for a in self.approvals.values():
            if a.id == exclude_id or a.intent.kind != IntentKind.TRANSFER:
                continue
            if a.status not in (ApprovalStatus.PENDING, ApprovalStatus.SIGNED):
                continue
            if a.intent.created_at.date() != today:
                continue
            if to is not None and a.intent.to.lower() != to.lower():
                continue
            total += a.decision.usd_value or 0.0
        return round(total, 2)
