"""The MCP adapter against scripts/fake_tangem_mcp.py over stdio: the same path Tangem's MCP will use."""
import asyncio

from app.config import BASE_DIR
from app.models import IntentKind, TxIntent
from app.wallet.tangem_mcp import TangemMcpWallet, _fill


def test_fill_keeps_types_and_drops_none():
    out = _fill({"to": "{to}", "amount": "{amount}", "memo": "{memo}", "label": "pay {to}"},
                {"to": "0xabc", "amount": 1.5, "memo": None})
    assert out == {"to": "0xabc", "amount": 1.5, "label": "pay 0xabc"}


def test_adapter_reads_and_signs_via_fake_server():
    async def main():
        wallet = TangemMcpWallet(
            transport="stdio", command="python", args=["scripts/fake_tangem_mcp.py"],
            tool_map_path=BASE_DIR / "mcp_tools.json", cwd=BASE_DIR,
        )
        await wallet.start()
        try:
            assert {"get_balances", "get_transactions", "send_transaction"} <= set(wallet.available)
            balances = {(b.token, b.chain): b.amount for b in await wallet.get_balances()}
            assert balances[("USDC", "ethereum-sepolia")] == 500
            history = await wallet.get_history()
            assert history[0].to.startswith("0xA11CE")

            ref = await wallet.sign_and_send(TxIntent(
                kind=IntentKind.TRANSFER, to="0xA11CE00000000000000000000000000000000001",
                amount=12.5, token="USDC", chain="ethereum-sepolia",
            ))
            assert ref.startswith("0x") and len(ref) == 66
            balances = {(b.token, b.chain): b.amount for b in await wallet.get_balances()}
            assert balances[("USDC", "ethereum-sepolia")] == 487.5
        finally:
            await wallet.stop()
    asyncio.run(main())
