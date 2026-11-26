"""ClaudeAgent tool loop with a fake Anthropic client (no network, no key)."""
import asyncio
from types import SimpleNamespace as NS

from app.agent import ClaudeAgent
from app.config import BASE_DIR
from app.models import ApprovalStatus
from app.policy import Policy
from app.service import TapGuardService
from app.wallet.mock import MockWallet


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


def test_agent_proposes_but_cannot_sign():
    async def main():
        svc = TapGuardService(MockWallet(tap_delay=0), Policy.load(BASE_DIR / "policy.json"))
        agent = ClaudeAgent.__new__(ClaudeAgent)  # skip real client construction
        agent.svc, agent.model, agent.history = svc, "test-model", []
        from app.agent import ToolBox
        agent.tools = ToolBox(svc)
        fake = FakeMessages([
            NS(stop_reason="tool_use", content=[
                NS(type="text", text="Proposing that now."),
                NS(type="tool_use", id="t1", name="propose_payment",
                   input={"to": "alice", "amount": 20, "token": "USDC"}),
            ]),
            NS(stop_reason="end_turn", content=[NS(type="text", text="Queued 20 USDC to alice. Tap your card to sign.")]),
        ])
        agent.client = NS(messages=fake)

        out = await agent.chat("pay alice 20 usdc")
        assert "Tap your card" in out["reply"]
        assert out["events"][0]["output"]["verdict"] == "allow"
        # Nothing signed: it's waiting for the tap
        assert [a.status for a in svc.store.all()] == [ApprovalStatus.PENDING]
        # The model was never offered a signing tool
        names = {t["name"] for t in fake.calls[0]["tools"]}
        assert not any("sign" in n or "send" in n for n in names)
        # History alternates user/assistant correctly for the next turn
        assert [m["role"] for m in agent.history] == ["user", "assistant", "user", "assistant"]
    asyncio.run(main())
