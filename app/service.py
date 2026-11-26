"""The one place proposals turn into decisions and signatures. Agent and API both go through here."""
from __future__ import annotations

import asyncio

from .explainer import explain
from .models import Approval, ApprovalStatus, IntentKind, TxIntent, Verdict, utcnow
from .policy import Policy, PolicyEngine
from .risk import assess_risks, short
from .store import Store
from .wallet.base import WalletProvider


class PolicyError(Exception):
    """Raised for requests the owner/agent shouldn't be able to make. Maps to HTTP 400."""


class TapGuardService:
    def __init__(self, wallet: WalletProvider, policy: Policy, store: Store | None = None):
        self.wallet = wallet
        self.policy = policy
        self.engine = PolicyEngine(policy)
        self.store = store or Store()
        self._sign_lock = asyncio.Lock()  # one tap at a time

    # ---------- helpers ----------
    def resolve_recipient(self, name_or_address: str) -> tuple[str, str | None]:
        contact = self.policy.find_contact(name_or_address)
        if contact:
            return contact.address, contact.name
        return name_or_address.strip(), None

    async def _decide(self, intent: TxIntent, exclude_id: str | None = None, user_text: str | None = None):
        balances = await self.wallet.get_balances()
        history = await self.wallet.get_history(limit=100)
        # Only addresses YOU paid count as known. Incoming dust from a scammer must not whitelist them.
        known = {h.to.lower() for h in history if h.direction == "out"}
        known |= {a.intent.to.lower() for a in self.store.approvals.values() if a.status == ApprovalStatus.SIGNED}
        flags = assess_risks(
            intent, self.policy.contacts, known, balances,
            user_text=user_text if self.policy.require_user_supplied_recipients else None,
        )
        return self.engine.evaluate(
            intent, flags=flags,
            spent_today_usd=self.store.spent_today_usd(exclude_id),
            spent_to_recipient_usd=self.store.spent_today_usd(exclude_id, to=intent.to),
        )

    def _pending(self, approval_id: str) -> Approval:
        a = self.store.get(approval_id)
        if a is None:
            raise PolicyError(f"No request with id {approval_id}.")
        if a.status != ApprovalStatus.PENDING:
            raise PolicyError(f"Request {approval_id} is already {a.status.value}.")
        return a

    # ---------- actions ----------
    async def propose(
        self, *, kind: IntentKind, to: str, amount: float | None, token: str, chain: str | None = None,
        memo: str = "", user_text: str | None = None,
    ) -> Approval:
        """user_text = what the owner actually typed this turn (for the provenance check)."""
        address, label = self.resolve_recipient(to)
        intent = TxIntent(
            kind=kind, to=address, amount=amount, token=token.upper(),
            chain=(chain or self.policy.default_chain).lower(), memo=memo,
        )
        decision = await self._decide(intent, user_text=user_text)
        approval = Approval(
            intent=intent,
            decision=decision,
            explanation=explain(intent, decision.usd_value, label),
            recipient_label=label,
            user_text=user_text,
            status=ApprovalStatus.BLOCKED if decision.verdict == Verdict.BLOCK else ApprovalStatus.PENDING,
        )
        self.store.add(approval)
        self.store.log("proposed", f"[{decision.verdict.value.upper()}] {approval.explanation}")
        return approval

    async def approve(self, approval_id: str, acknowledge_warnings: bool = False) -> Approval:
        async with self._sign_lock:
            a = self._pending(approval_id)
            if a.decision.verdict == Verdict.REVIEW and not acknowledge_warnings:
                raise PolicyError("This one has warnings. Read them and confirm before tapping.")

            # Re-check at signing time: limits, balances or history may have changed since it was queued.
            fresh = await self._decide(a.intent, exclude_id=a.id, user_text=a.user_text)
            if fresh.verdict == Verdict.BLOCK:
                a.decision, a.status, a.resolved_at = fresh, ApprovalStatus.BLOCKED, utcnow()
                self.store.log("blocked_at_sign", f"Blocked at signing time: {'; '.join(fresh.reasons)}")
                return a
            if fresh.verdict == Verdict.REVIEW and a.decision.verdict == Verdict.ALLOW and not acknowledge_warnings:
                a.decision = fresh
                raise PolicyError("Something changed since this was queued. Read the new warnings and confirm.")

            self.store.log("tap_requested", f"Waiting for card tap: {a.explanation}")
            try:
                ref = await self.wallet.sign_and_send(a.intent)  # <- the physical tap happens in here
            except Exception as e:  # noqa: BLE001 - surface any wallet/MCP error to the UI
                a.status, a.error, a.resolved_at = ApprovalStatus.FAILED, str(e), utcnow()
                self.store.log("failed", f"Signing failed: {e}")
                return a

            a.status, a.tx_ref, a.resolved_at = ApprovalStatus.SIGNED, ref, utcnow()
            if ref and ref.startswith("http"):
                self.store.log("signed", f"Tap ready — open this link in the Tangem app: {ref}")
            else:
                self.store.log("signed", f"Signed after tap ({short(ref)}): {a.explanation}")
            return a

    def reject(self, approval_id: str) -> Approval:
        a = self._pending(approval_id)
        a.status, a.resolved_at = ApprovalStatus.REJECTED, utcnow()
        self.store.log("rejected", f"You rejected: {a.explanation}")
        return a

    def update_limits(self, **limits: float | None) -> None:
        changed = []
        for field in ("per_tx_limit_usd", "daily_limit_usd", "review_above_usd"):
            value = limits.get(field)
            if value is None:
                continue
            if value < 0:
                raise PolicyError(f"{field} can't be negative.")
            setattr(self.policy, field, float(value))
            changed.append(f"{field}=${value:,.2f}")
        if changed:
            self.store.log("policy", "Limits updated: " + ", ".join(changed))

    async def state(self) -> dict:
        balances = await self.wallet.get_balances()
        p = self.policy
        return {
            "wallet": self.wallet.name,
            "balances": [
                {**b.model_dump(), "usd": round(b.amount * p.price(b.token), 2) if p.price(b.token) else None}
                for b in balances
            ],
            "approvals": [a.model_dump(mode="json") for a in self.store.all()],
            "audit": [e.model_dump(mode="json") for e in reversed(self.store.audit[-50:])],
            "limits": {
                "per_tx_limit_usd": p.per_tx_limit_usd,
                "daily_limit_usd": p.daily_limit_usd,
                "review_above_usd": p.review_above_usd,
                "spent_today_usd": self.store.spent_today_usd(),
            },
            "contacts": [c.model_dump() for c in p.contacts],
            "allowed_chains": p.allowed_chains,
            "allowed_tokens": p.allowed_tokens,
        }
