"""Wallet adapter that talks to an MCP server: the fake one now, Tangem's on the night.

Tool names and argument shapes live in mcp_tools.json, so wiring up the real server should be
a config edit plus (maybe) a tweak to the _normalise_* functions below. Written for the
Python MCP SDK 2.x (`mcp.Client`).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from ..models import Balance, PastTx, TxIntent
from .base import WalletProvider

# Tangem's server rejects the SDK's newest handshake version; pin an older one.
import mcp.client.session as _mcp_session  # noqa: E402

_mcp_session.LATEST_HANDSHAKE_VERSION = "2024-11-05"

from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client  # noqa: E402

from .tangem_oauth import get_tangem_token  # noqa: E402

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _fill(template: Any, values: dict[str, Any]) -> Any:
    """Substitute {placeholders}. A lone placeholder keeps the value's type; None values are dropped."""
    if isinstance(template, dict):
        out = {k: _fill(v, values) for k, v in template.items()}
        return {k: v for k, v in out.items() if v is not None}
    if isinstance(template, list):
        return [_fill(v, values) for v in template]
    if isinstance(template, str):
        whole = _PLACEHOLDER.fullmatch(template)
        if whole:
            return values.get(whole.group(1))
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), template)
    return template


def _payload(result: Any) -> Any:
    """Pull usable data out of a CallToolResult (structured content first, then JSON text)."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    texts = [c.text for c in (result.content or []) if getattr(c, "type", None) == "text"]
    parsed = []
    for t in texts:
        try:
            parsed.append(json.loads(t))
        except (json.JSONDecodeError, TypeError):
            parsed.append(t)
    if len(parsed) == 1:
        return parsed[0]
    return parsed


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _as_list(data: Any, *keys: str) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


# ---- Edit these two if Tangem's response shapes differ ----
def _normalise_balances(data: Any) -> list[Balance]:
    out = []
    for item in _as_list(data, "balances", "tokens", "assets", "items"):
        if not isinstance(item, dict):
            continue
        out.append(Balance(
            token=str(_first(item, "token", "symbol", "currency", "asset", default="?")).upper(),
            chain=str(_first(item, "chain", "network", "blockchain", default="unknown")).lower(),
            amount=float(_first(item, "amount", "balance", "value", default=0)),
        ))
    return out


def _normalise_history(data: Any) -> list[PastTx]:
    out = []
    for item in _as_list(data, "transactions", "history", "items"):
        if not isinstance(item, dict):
            continue
        out.append(PastTx(
            to=str(_first(item, "to", "recipient", "destination", default="")),
            amount=float(_first(item, "amount", "value", default=0)),
            token=str(_first(item, "token", "symbol", "currency", default="?")).upper(),
            chain=str(_first(item, "chain", "network", "blockchain", default="unknown")).lower(),
            ref=str(_first(item, "hash", "txHash", "tx_hash", "id", default="")),
            direction="in" if str(_first(item, "direction", "type", default="out")).lower()
            in {"in", "incoming", "receive", "received", "deposit"} else "out",
            from_address=str(_first(item, "from", "sender", "from_address", default="")),
            memo=str(_first(item, "memo", "note", "message", "comment", default="")),
        ))
    return out


def _tx_ref(data: Any) -> str:
    if isinstance(data, dict):
        return str(_first(data, "hash", "txHash", "tx_hash", "signature", "id", default=json.dumps(data)))
    return str(data)


class TangemMcpWallet(WalletProvider):
    name = "mcp"

    def __init__(
        self,
        *,
        transport: str = "stdio",
        command: str = "python",
        args: list[str] | None = None,
        url: str = "",
        auth_token: str = "",
        tool_map_path: Path,
        cwd: Path | None = None,
    ):
        self.transport = transport
        self.command = sys.executable if command in ("python", "python3") else command
        self.args = args or []
        self.url = url
        self.auth_token = auth_token
        self.cwd = cwd
        self.tool_map: dict[str, dict] = {
            k: v for k, v in json.loads(Path(tool_map_path).read_text()).items() if not k.startswith("_")
        }
        self.client: Client | None = None
        self.available: dict[str, Any] = {}  # tool name -> Tool
        self._last_intent_id: str | None = None

    @classmethod
    def from_settings(cls, settings) -> "TangemMcpWallet":
        from ..config import BASE_DIR

        return cls(
            transport=settings.mcp_transport,
            command=settings.mcp_command,
            args=settings.mcp_args,
            url=settings.mcp_url,
            auth_token=settings.mcp_auth_token,
            tool_map_path=settings.mcp_tool_map,
            cwd=BASE_DIR,
        )

    def _server(self):
        if self.transport == "stdio":
            return StdioServerParameters(
                command=self.command, args=self.args, env=dict(os.environ),
                cwd=str(self.cwd) if self.cwd else None,
            )
        headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else None
        if self.transport == "http":
            if not headers:
                return self.url  # Client treats a plain URL as Streamable HTTP
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

            return streamable_http_client(self.url, http_client=create_mcp_http_client(headers=headers))
        if self.transport == "sse":
            from mcp.client.sse import sse_client

            return sse_client(self.url, headers=headers)
        raise ValueError(f"Unknown MCP_TRANSPORT '{self.transport}' (use stdio, http or sse)")

    async def start(self) -> None:
        if self.transport == "oauth":
            token = await get_tangem_token()
            http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
            server = streamable_http_client(self.url, http_client=http_client)
            self.client = Client(server, mode="legacy")
        else:
            self.client = Client(self._server())
        await self.client.__aenter__()
        tools = await self.client.list_tools()
        self.available = {t.name: t for t in tools.tools}
        if self.transport != "oauth":
            missing = [spec["tool"] for spec in self.tool_map.values() if spec["tool"] not in self.available]
            if missing:
                print(f"[tapguard] WARNING: mapped MCP tools not found on server: {missing}. "
                      f"Server has: {sorted(self.available)}. Edit mcp_tools.json.", file=sys.stderr)

    async def stop(self) -> None:
        if self.client:
            await self.client.__aexit__(None, None, None)
            self.client = None

    async def _call(self, op: str, **values: Any) -> Any:
        if self.client is None:
            raise RuntimeError("MCP client not started")
        spec = self.tool_map[op]
        args = _fill(spec.get("args", {}), values)
        result = await self.client.call_tool(spec["tool"], args)
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            raise RuntimeError(f"MCP tool {spec['tool']} failed: {_payload(result)}")
        return _payload(result)

    async def _call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Call a Tangem MCP tool directly (bypasses the generic tool_map)."""
        if self.client is None:
            raise RuntimeError("MCP client not started")
        result = await self.client.call_tool(name, args)
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            raise RuntimeError(f"MCP tool {name} failed: {_payload(result)}")
        return _payload(result)

    async def _tokens(self) -> list[dict]:
        data = await self._call_tool("wallets__get_tokens", {})
        return data.get("tokens", []) if isinstance(data, dict) else []

    @staticmethod
    def _network_id(chain: str) -> str:
        """Map TapGuard's chain name to Tangem's networkId."""
        return {
            "ethereum-sepolia": "ethereum",
            "ethereum": "ethereum",
            "polygon-amoy": "polygon-pos",
            "polygon-pos": "polygon-pos",
            "bitcoin": "bitcoin",
        }.get(chain.lower(), chain.lower())

    @staticmethod
    def _chain_name(network_id: str) -> str:
        """Map Tangem's networkId back to TapGuard's chain name (reverse of _network_id)."""
        return {
            "ethereum": "ethereum-sepolia",
            "polygon-pos": "polygon-amoy",
            "bitcoin": "bitcoin",
        }.get(network_id.lower(), network_id.lower())

    async def get_balances(self) -> list[Balance]:
        if self.transport == "oauth":
            return await self._tangem_balances()
        return _normalise_balances(await self._call("get_balances"))

    async def _tangem_balances(self) -> list[Balance]:
        out: list[Balance] = []
        for t in await self._tokens():
            network = t.get("networkId")
            symbol = str(t.get("symbol") or t.get("name") or "?").upper()
            decimals = int(t.get("decimals") or 0)
            address = (t.get("addresses") or [None])[0]
            contract = t.get("contractAddress") or "0"  # "0" = native coin
            if not network or not address:
                continue
            try:
                data = await self._call_tool("transactions__get_balances", {
                    "request": {
                        "networkId": network,
                        "walletAddress": address,
                        "tokens": [{"contractAddress": contract, "decimals": decimals}],
                    },
                })
            except Exception:
                continue  # network not readable by get_balances; skip it
            for b in (data.get("balances") or []) if isinstance(data, dict) else []:
                raw = b.get("rawAmount") if isinstance(b, dict) else None
                if raw is None:
                    continue
                amount = float(raw) / (10 ** decimals) if decimals else float(raw)
                out.append(Balance(token=symbol, chain=self._chain_name(network), amount=amount))
        return out

    async def get_history(self, limit: int = 20) -> list[PastTx]:
        if self.transport == "oauth":
            # Tangem's MCP has no general transfer-history tool.
            return []
        return _normalise_history(await self._call("get_history", limit=limit))

    async def sign_and_send(self, intent: TxIntent) -> str:
        if self.transport == "oauth":
            return await self._tangem_send(intent)
        data = await self._call(
            "sign_and_send",
            to=intent.to, amount=intent.amount, token=intent.token,
            chain=intent.chain, kind=intent.kind.value, memo=intent.memo or None,
        )
        return _tx_ref(data)

    async def _tangem_send(self, intent: TxIntent) -> str:
        network = self._network_id(intent.chain)
        token = next(
            (t for t in await self._tokens()
             if t.get("networkId") == network
             and str(t.get("symbol") or "").upper() == intent.token.upper()),
            None,
        )
        if token is None:
            raise RuntimeError(f"No {intent.token} on {network} in the approved wallet")
        from_address = (token.get("addresses") or [None])[0]
        token_id = token.get("tokenId")
        if not from_address or not token_id:
            raise RuntimeError(f"{intent.token} on {network} has no sendable address/tokenId")
        data = await self._call_tool("transactions__send_transaction", {
            "request": {
                "networkId": network,
                "tokenId": token_id,
                "fromAddress": from_address,
                "toAddress": intent.to,
                "amount": str(intent.amount),
                "sendNotification": True,
                "memo": intent.memo or None,
            },
        })
        deeplink = (data or {}).get("deeplink", "") if isinstance(data, dict) else ""
        self._last_intent_id = (data or {}).get("intentId") if isinstance(data, dict) else None
        if not deeplink:
            raise RuntimeError(f"send_transaction returned no deeplink: {data}")
        return deeplink

    async def transaction_status(self, intent_id: str | None = None) -> dict:
        return await self._call_tool(
            "transactions__get_transaction_status",
            {"intentId": intent_id or self._last_intent_id},
        )
