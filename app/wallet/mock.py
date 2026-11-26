"""Offline wallet with fake balances. Lets you build and demo without the card or Wi-Fi."""
from __future__ import annotations

import asyncio
import hashlib
import time

from ..models import Balance, IntentKind, PastTx, TxIntent, utcnow
from .base import WalletProvider

ALICE = "0xA11CE00000000000000000000000000000000001"


class MockWallet(WalletProvider):
    name = "mock"

    def __init__(self, tap_delay: float = 1.2):
        self.tap_delay = tap_delay
        self.balances: dict[tuple[str, str], float] = {
            ("USDC", "ethereum-sepolia"): 500.0,
            ("ETH", "ethereum-sepolia"): 0.25,
            ("USDC", "polygon-amoy"): 120.0,
        }
        self.history: list[PastTx] = [
            PastTx(to=ALICE, amount=15, token="USDC", chain="ethereum-sepolia", ref="0xmock01", timestamp=utcnow()),
        ]

    def apply_setup(self, setup: dict) -> None:
        """Used by evals: replace balances / add history entries (incl. attacker-controlled ones)."""
        if "balances" in setup:
            self.balances = {(b["token"], b["chain"]): float(b["amount"]) for b in setup["balances"]}
        for h in setup.get("history", []):
            self.history.append(PastTx(timestamp=utcnow(), **h))

    async def get_balances(self) -> list[Balance]:
        return [Balance(token=t, chain=c, amount=round(a, 8)) for (t, c), a in self.balances.items()]

    async def get_history(self, limit: int = 20) -> list[PastTx]:
        return list(reversed(self.history))[:limit]

    async def sign_and_send(self, intent: TxIntent) -> str:
        await asyncio.sleep(self.tap_delay)  # pretend the owner is tapping the card
        if intent.kind == IntentKind.TRANSFER:
            key = (intent.token.upper(), intent.chain.lower())
            have = self.balances.get(key, 0.0)
            if (intent.amount or 0) > have:
                raise RuntimeError(f"Insufficient {intent.token} on {intent.chain}")
            self.balances[key] = have - (intent.amount or 0)
        ref = "0x" + hashlib.sha256(f"{intent.id}{time.time()}".encode()).hexdigest()
        if intent.kind == IntentKind.TRANSFER:
            self.history.append(PastTx(
                to=intent.to, amount=intent.amount or 0, token=intent.token,
                chain=intent.chain, ref=ref, timestamp=utcnow(),
            ))
        return ref
