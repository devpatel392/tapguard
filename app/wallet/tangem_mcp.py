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
        self.client = Client(self._server())
        await self.client.__aenter__()
        tools = await self.client.list_tools()
        self.available = {t.name: t for t in tools.tools}
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

    async def get_balances(self) -> list[Balance]:
        return _normalise_balances(await self._call("get_balances"))

    async def get_history(self, limit: int = 20) -> list[PastTx]:
        return _normalise_history(await self._call("get_history", limit=limit))

    async def sign_and_send(self, intent: TxIntent) -> str:
        data = await self._call(
            "sign_and_send",
            to=intent.to, amount=intent.amount, token=intent.token,
            chain=intent.chain, kind=intent.kind.value, memo=intent.memo or None,
        )
        return _tx_ref(data)
