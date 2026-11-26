"""First thing to run on the night: list every tool the Tangem MCP exposes, with its input schema.

    python scripts/inspect_mcp.py                      # uses MCP_* settings from .env
    python scripts/inspect_mcp.py --call get_balances  # try one tool (read-only ones only!)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.wallet.tangem_mcp import TangemMcpWallet, _payload  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", help="tool name to call")
    ap.add_argument("--args", default="{}", help="JSON arguments for --call")
    opts = ap.parse_args()

    wallet = TangemMcpWallet.from_settings(settings)
    await wallet.start()
    try:
        if opts.call:
            result = await wallet.client.call_tool(opts.call, json.loads(opts.args))
            print(json.dumps(_payload(result), indent=2, default=str))
            return
        print(f"{len(wallet.available)} tools on {settings.mcp_transport} server\n")
        for tool in wallet.available.values():
            schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {})
            print(f"=== {tool.name}")
            if tool.description:
                print(tool.description.strip())
            print(json.dumps(schema, indent=2))
            print()
    finally:
        await wallet.stop()


if __name__ == "__main__":
    asyncio.run(main())
