"""Plain-English description of what the card is about to sign.

Deliberately not LLM-generated: the text on the tap screen has to match the transaction exactly.
"""
from __future__ import annotations

from .models import IntentKind, TxIntent
from .risk import short


def fmt_amount(x: float) -> str:
    return f"{x:,.6f}".rstrip("0").rstrip(".")


def explain(intent: TxIntent, usd_value: float | None, recipient_label: str | None) -> str:
    who = f"{recipient_label} ({short(intent.to)})" if recipient_label else short(intent.to)
    usd = f" (~${usd_value:,.2f})" if usd_value is not None else ""

    if intent.kind == IntentKind.TRANSFER:
        text = f"Send {fmt_amount(intent.amount or 0)} {intent.token}{usd} to {who} on {intent.chain}."
    elif intent.amount is None:
        text = f"Let {who} spend ANY amount of your {intent.token} on {intent.chain}, with no expiry."
    else:
        text = f"Let {who} spend up to {fmt_amount(intent.amount)} {intent.token}{usd} from your wallet on {intent.chain}."

    if intent.memo:
        text += f' Note: "{intent.memo}".'
    return text
