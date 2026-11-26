from __future__ import annotations

from .base import WalletProvider
from .mock import MockWallet


def make_wallet(settings) -> WalletProvider:
    if settings.wallet_backend == "mcp":
        from .tangem_mcp import TangemMcpWallet

        return TangemMcpWallet.from_settings(settings)
    return MockWallet(tap_delay=settings.mock_tap_delay)


__all__ = ["WalletProvider", "MockWallet", "make_wallet"]
