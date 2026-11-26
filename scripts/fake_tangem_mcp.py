"""A stand-in Tangem MCP server so you can rehearse the MCP path before the night.

Run by TapGuard over stdio when WALLET_BACKEND=mcp (the default MCP_ARGS point here).
The real server's tool names/shapes will differ; that's what mcp_tools.json is for.
Never print to stdout here: stdout is the MCP channel.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time

from mcp.server.mcpserver import MCPServer

server = MCPServer("fake-tangem")
TAP_DELAY = float(os.getenv("FAKE_TAP_DELAY", "1.5"))

BALANCES = {
    ("USDC", "ethereum-sepolia"): 500.0,
    ("ETH", "ethereum-sepolia"): 0.25,
    ("USDC", "polygon-amoy"): 120.0,
}
HISTORY: list[dict] = [
    {"to": "0xA11CE00000000000000000000000000000000001", "amount": 15, "token": "USDC",
     "chain": "ethereum-sepolia", "hash": "0xfake01"},
]


@server.tool()
def get_balances() -> dict:
    """Token balances on the user's Tangem wallet."""
    return {"balances": [{"symbol": t, "network": c, "balance": a} for (t, c), a in BALANCES.items()]}


@server.tool()
def get_transactions(limit: int = 20) -> dict:
    """Recent outgoing transactions, newest first."""
    return {"transactions": list(reversed(HISTORY))[:limit]}


@server.tool()
def send_transaction(to: str, token: str, chain: str, amount: float | None = None, kind: str = "transfer") -> dict:
    """Sign with the Tangem card (requires a physical tap) and broadcast."""
    print(f"[fake-tangem] tap your card to sign: {kind} {amount} {token} -> {to} on {chain}", file=sys.stderr)
    time.sleep(TAP_DELAY)
    if kind == "transfer":
        key = (token.upper(), chain.lower())
        if (amount or 0) > BALANCES.get(key, 0):
            raise ValueError(f"Insufficient {token} on {chain}")
        BALANCES[key] -= amount or 0
    tx_hash = "0x" + hashlib.sha256(f"{to}{amount}{time.time()}".encode()).hexdigest()
    if kind == "transfer":
        HISTORY.append({"to": to, "amount": amount, "token": token, "chain": chain, "hash": tx_hash})
    return {"txHash": tx_hash, "status": "signed"}


if __name__ == "__main__":
    server.run()  # stdio
