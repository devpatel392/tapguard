"""Risk checks on a proposed transaction. These produce flags; policy.py decides what they mean."""
from __future__ import annotations

from collections.abc import Iterable

from .models import Balance, Contact, IntentKind, RiskFlag, Severity, TxIntent


def short(addr: str, head: int = 6, tail: int = 4) -> str:
    return f"{addr[:head]}…{addr[-tail:]}" if len(addr) > head + tail + 2 else addr


def looks_like(a: str, b: str) -> bool:
    """Address-poisoning check: same start and end as a known address, different middle.

    Scammers generate vanity addresses that match the first and last few characters of an
    address you've used, then send you a dust transaction so it shows up in your history.
    """
    a, b = a.lower(), b.lower()
    if a == b or len(a) < 12 or len(b) < 12:
        return False
    return a[:6] == b[:6] and a[-4:] == b[-4:]


def assess_risks(
    intent: TxIntent,
    contacts: Iterable[Contact],
    known_recipients: set[str],
    balances: Iterable[Balance],
    user_text: str | None = None,
) -> list[RiskFlag]:
    contacts = list(contacts)
    flags: list[RiskFlag] = []
    to = intent.to.lower()
    contact_addrs = {c.address.lower() for c in contacts}
    is_known = to in contact_addrs or to in known_recipients

    # 1. Lookalike of a contact or a past recipient
    if not is_known:
        seen: set[str] = set()
        candidates = [(c.address, f"{c.name}'s address") for c in contacts]
        candidates += [(r, "an address you've paid before") for r in sorted(known_recipients)]
        for addr, who in candidates:
            if addr.lower() in seen or not looks_like(intent.to, addr):
                continue
            seen.add(addr.lower())
            flags.append(RiskFlag(
                code="lookalike_address",
                severity=Severity.DANGER,
                # Longer prefix here so the differing middle is visible.
                message=f"{short(intent.to, 10, 6)} looks like {who} ({short(addr, 10, 6)}): same start "
                        "and end, different middle. This is a common address-poisoning scam.",
            ))

    # 2. Provenance: an unknown address the owner never typed came from untrusted data
    #    (a memo, token name, web page...). Classic prompt-injection payload, so block it.
    if not is_known and user_text is not None and to not in user_text.lower():
        flags.append(RiskFlag(
            code="untrusted_recipient",
            severity=Severity.DANGER,
            message=f"{short(intent.to)} didn't come from you or your contacts. The agent picked it up "
                    "from wallet data (a memo, token name or similar), which is how prompt-injection attacks work.",
        ))

    # 3. Never paid before
    if not is_known:
        flags.append(RiskFlag(
            code="new_recipient",
            severity=Severity.WARN,
            message=f"You've never sent anything to {short(intent.to)} before.",
        ))

    # 4. Token approvals (allowances)
    if intent.kind == IntentKind.APPROVE:
        if intent.amount is None:
            flags.append(RiskFlag(
                code="unlimited_approval",
                severity=Severity.DANGER,
                message=f"Unlimited approval: {short(intent.to)} could take all of your {intent.token} "
                        "at any time until you revoke it.",
            ))
        else:
            flags.append(RiskFlag(
                code="token_approval",
                severity=Severity.WARN,
                message=f"{short(intent.to)} will be able to spend up to {intent.amount:g} {intent.token} "
                        "from your wallet without asking again.",
            ))

    # 5. Balance checks
    if intent.kind == IntentKind.TRANSFER and intent.amount:
        have = sum(
            b.amount for b in balances
            if b.token.upper() == intent.token.upper() and b.chain.lower() == intent.chain.lower()
        )
        if intent.amount > have:
            flags.append(RiskFlag(
                code="insufficient_funds",
                severity=Severity.DANGER,
                message=f"You only have {have:g} {intent.token.upper()} on {intent.chain}.",
            ))
        elif intent.amount > 0.5 * have:
            flags.append(RiskFlag(
                code="large_share",
                severity=Severity.WARN,
                message=f"This is {intent.amount / have:.0%} of your {intent.token.upper()} on {intent.chain}.",
            ))

    return flags
