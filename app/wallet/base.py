"""The only interface the rest of the app knows about. Swap mock <-> Tangem MCP behind it."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Balance, PastTx, TxIntent


class WalletProvider(ABC):
    name: str = "wallet"

    async def start(self) -> None:  # open connections
        pass

    async def stop(self) -> None:  # close connections
        pass

    @abstractmethod
    async def get_balances(self) -> list[Balance]: ...

    @abstractmethod
    async def get_history(self, limit: int = 20) -> list[PastTx]: ...

    @abstractmethod
    async def sign_and_send(self, intent: TxIntent) -> str:
        """Sign with the card and broadcast. Returns a tx hash/reference.

        With a real Tangem card this is where the owner taps the card to the phone.
        Only TapGuardService calls this, and only after the policy engine and the owner said yes.
        """
