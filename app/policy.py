"""Hard spending rules. Deterministic on purpose: the LLM never decides what's allowed."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .models import Contact, IntentKind, PolicyDecision, RiskFlag, Severity, TxIntent, Verdict


class Policy(BaseModel):
    per_tx_limit_usd: float = 100
    daily_limit_usd: float = 250
    per_recipient_daily_limit_usd: float = 100  # stops splitting one big payment into small ones
    review_above_usd: float = 50
    allowed_chains: list[str] = Field(default_factory=list)  # empty = any chain
    allowed_tokens: list[str] = Field(default_factory=list)  # empty = any token
    block_unlimited_approvals: bool = True
    review_new_recipients: bool = True
    # Block payments to unknown addresses the owner never typed (i.e. the agent got them from tool data).
    require_user_supplied_recipients: bool = True
    contacts: list[Contact] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list)
    prices_usd: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        return cls.model_validate_json(Path(path).read_text())

    def price(self, token: str) -> float | None:
        return self.prices_usd.get(token.upper())

    def find_contact(self, name_or_address: str) -> Contact | None:
        key = name_or_address.strip().lower()
        for c in self.contacts:
            if c.name.lower() == key or c.address.lower() == key:
                return c
        return None

    @property
    def default_chain(self) -> str:
        return self.allowed_chains[0] if self.allowed_chains else "ethereum"


class PolicyEngine:
    def __init__(self, policy: Policy):
        self.policy = policy

    def evaluate(
        self, intent: TxIntent, *, flags: list[RiskFlag], spent_today_usd: float, spent_to_recipient_usd: float = 0.0
    ) -> PolicyDecision:
        p = self.policy
        blocks: list[str] = []
        reviews: list[str] = []
        token, chain = intent.token.upper(), intent.chain.lower()

        # 1. Allowlists / blocklist
        if p.allowed_chains and chain not in {c.lower() for c in p.allowed_chains}:
            blocks.append(f"Chain '{intent.chain}' isn't on your allowed list.")
        if p.allowed_tokens and token not in {t.upper() for t in p.allowed_tokens}:
            blocks.append(f"Token '{intent.token}' isn't on your allowed list.")
        if intent.to.lower() in {a.lower() for a in p.blocklist}:
            blocks.append("That address is on your blocklist.")

        # 2. Value the request in USD so limits mean something
        usd: float | None = None
        if intent.amount is not None:
            if intent.amount <= 0:
                blocks.append("Amount must be greater than zero.")
            price = p.price(token)
            if price is None:
                blocks.append(f"No price for {token}, so your limits can't be enforced.")
            else:
                usd = round(intent.amount * price, 2)
        elif intent.kind == IntentKind.TRANSFER:
            blocks.append("A payment needs an amount.")

        # 3. Spending limits
        if usd is not None:
            what = "payment" if intent.kind == IntentKind.TRANSFER else "approval"
            if usd > p.per_tx_limit_usd:
                blocks.append(f"${usd:,.2f} is over your ${p.per_tx_limit_usd:,.2f} per-{what} limit.")
            if intent.kind == IntentKind.TRANSFER:
                # Pending payments count too, so the agent can't queue ten small ones to dodge the cap.
                if spent_today_usd + usd > p.daily_limit_usd:
                    blocks.append(
                        f"This would take today's total to ${spent_today_usd + usd:,.2f}, "
                        f"over your ${p.daily_limit_usd:,.2f} daily limit."
                    )
                if spent_to_recipient_usd + usd > p.per_recipient_daily_limit_usd:
                    blocks.append(
                        f"You'd be sending ${spent_to_recipient_usd + usd:,.2f} to this address today, over your "
                        f"${p.per_recipient_daily_limit_usd:,.2f} per-recipient daily limit."
                    )
                if usd > p.review_above_usd:
                    reviews.append(f"${usd:,.2f} is above your ${p.review_above_usd:,.2f} review threshold.")

        # 4. Risk flags from risk.py
        for f in flags:
            if f.code == "unlimited_approval":
                (blocks if p.block_unlimited_approvals else reviews).append(f.message)
            elif f.code == "new_recipient" and not p.review_new_recipients:
                continue
            elif f.severity == Severity.DANGER:
                blocks.append(f.message)
            elif f.severity == Severity.WARN:
                reviews.append(f.message)

        if blocks:
            return PolicyDecision(verdict=Verdict.BLOCK, reasons=blocks, flags=flags, usd_value=usd)
        if reviews:
            return PolicyDecision(verdict=Verdict.REVIEW, reasons=reviews, flags=flags, usd_value=usd)
        return PolicyDecision(
            verdict=Verdict.ALLOW,
            reasons=["Within your limits and going to someone you know."],
            flags=flags,
            usd_value=usd,
        )
