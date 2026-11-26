"""Policy + risk + service behaviour, against the mock wallet."""
import asyncio

import pytest

from app.config import BASE_DIR
from app.models import ApprovalStatus, IntentKind, Verdict
from app.policy import Policy
from app.service import PolicyError, TapGuardService
from app.wallet.mock import MockWallet

LOOKALIKE_ALICE = "0xA11CE77777777777777777777777777777770001"
STRANGER = "0x5EED123412341234123412341234123412341234"
BLOCKED = "0xDEAD000000000000000000000000000000000BAD"


def make() -> TapGuardService:
    return TapGuardService(MockWallet(tap_delay=0), Policy.load(BASE_DIR / "policy.json"))


def pay(svc, to, amount, token="USDC", chain="ethereum-sepolia"):
    return svc.propose(kind=IntentKind.TRANSFER, to=to, amount=amount, token=token, chain=chain)


def codes(a):
    return {f.code for f in a.decision.flags}


def test_small_payment_to_contact_is_allowed():
    async def main():
        a = await pay(make(), "alice", 20)
        assert a.decision.verdict == Verdict.ALLOW
        assert a.status == ApprovalStatus.PENDING
        assert a.explanation.startswith("Send 20 USDC (~$20.00) to alice")
    asyncio.run(main())


def test_over_per_payment_limit_is_blocked():
    async def main():
        a = await pay(make(), "bob", 150)
        assert a.decision.verdict == Verdict.BLOCK
        assert any("per-payment limit" in r for r in a.decision.reasons)
    asyncio.run(main())


def test_daily_cap_counts_pending_payments():
    async def main():
        svc = make()
        first = await pay(svc, "alice", 90)
        second = await pay(svc, "bob", 90)
        third = await pay(svc, "coffee-shop", 90)
        assert first.decision.verdict == Verdict.REVIEW  # above $50 review threshold
        assert second.decision.verdict == Verdict.REVIEW
        assert third.decision.verdict == Verdict.BLOCK
        assert any("daily limit" in r for r in third.decision.reasons)
    asyncio.run(main())


def test_splitting_to_one_recipient_hits_per_recipient_limit():
    async def main():
        svc = make()
        assert (await pay(svc, "bob", 150)).decision.verdict == Verdict.BLOCK
        assert (await pay(svc, "bob", 75)).decision.verdict == Verdict.REVIEW
        second_half = await pay(svc, "bob", 75, chain="polygon-amoy")  # other chain doesn't help
        assert second_half.decision.verdict == Verdict.BLOCK
        assert any("per-recipient" in r for r in second_half.decision.reasons)
    asyncio.run(main())


def test_provenance_blocks_addresses_the_owner_never_typed():
    async def main():
        svc = make()
        scam = "0x5CA3000000000000000000000000000000005CA3"
        injected = await svc.propose(kind=IntentKind.TRANSFER, to=scam, amount=10, token="USDC",
                                     user_text="any new transactions?")
        assert injected.decision.verdict == Verdict.BLOCK
        assert "untrusted_recipient" in codes(injected)
        typed = await svc.propose(kind=IntentKind.TRANSFER, to=scam, amount=10, token="USDC",
                                  user_text=f"send 10 usdc to {scam}")
        assert typed.decision.verdict == Verdict.REVIEW  # owner typed it: warn, don't block
    asyncio.run(main())


def test_incoming_dust_does_not_make_an_address_known():
    async def main():
        svc = make()
        poison = "0xA11CE77777777777777777777777777777770001"
        svc.wallet.apply_setup({"history": [{"to": "self", "direction": "in", "from_address": poison,
                                             "amount": 0.01, "token": "USDC", "chain": "ethereum-sepolia"}]})
        a = await svc.propose(kind=IntentKind.TRANSFER, to=poison, amount=20, token="USDC",
                              user_text="send 20 usdc to the address I transacted with most recently")
        assert a.decision.verdict == Verdict.BLOCK
        assert {"lookalike_address", "untrusted_recipient"} <= codes(a)
    asyncio.run(main())


def test_new_recipient_needs_review():
    async def main():
        a = await pay(make(), STRANGER, 10)
        assert a.decision.verdict == Verdict.REVIEW
        assert "new_recipient" in codes(a)
    asyncio.run(main())


def test_lookalike_address_is_blocked():
    async def main():
        a = await pay(make(), LOOKALIKE_ALICE, 10)
        assert a.decision.verdict == Verdict.BLOCK
        assert "lookalike_address" in codes(a)
        assert any("alice's address" in r for r in a.decision.reasons)
    asyncio.run(main())


def test_unlimited_approval_is_blocked():
    async def main():
        a = await make().propose(kind=IntentKind.APPROVE, to="coffee-shop", amount=None, token="USDC")
        assert a.decision.verdict == Verdict.BLOCK
        assert "unlimited_approval" in codes(a)
        assert "ANY amount" in a.explanation
    asyncio.run(main())


def test_capped_approval_needs_review():
    async def main():
        a = await make().propose(kind=IntentKind.APPROVE, to="coffee-shop", amount=25, token="USDC")
        assert a.decision.verdict == Verdict.REVIEW
        assert "token_approval" in codes(a)
    asyncio.run(main())


def test_split_approvals_to_one_spender_hit_per_recipient_limit():
    async def main():
        svc = make()
        first = await svc.propose(kind=IntentKind.APPROVE, to="bob", amount=90, token="USDC")
        assert first.decision.verdict == Verdict.REVIEW  # a single capped approval still warns
        second = await svc.propose(kind=IntentKind.APPROVE, to="bob", amount=90, token="USDC")
        assert second.decision.verdict == Verdict.BLOCK
        assert any("per-recipient" in r for r in second.decision.reasons)
    asyncio.run(main())


def test_disallowed_chain_and_blocklist():
    async def main():
        svc = make()
        assert (await pay(svc, "alice", 5, chain="ethereum")).decision.verdict == Verdict.BLOCK
        assert (await pay(svc, BLOCKED, 5)).decision.verdict == Verdict.BLOCK
        assert (await pay(svc, "alice", 5, token="DOGE")).decision.verdict == Verdict.BLOCK
    asyncio.run(main())


def test_insufficient_funds_is_blocked():
    async def main():
        svc = make()
        svc.wallet.balances[("USDC", "polygon-amoy")] = 5
        a = await pay(svc, "alice", 10, chain="polygon-amoy")
        assert a.decision.verdict == Verdict.BLOCK
        assert "insufficient_funds" in codes(a)
    asyncio.run(main())


def test_review_items_need_acknowledgement_then_sign():
    async def main():
        svc = make()
        a = await pay(svc, STRANGER, 10)
        with pytest.raises(PolicyError):
            await svc.approve(a.id)
        signed = await svc.approve(a.id, acknowledge_warnings=True)
        assert signed.status == ApprovalStatus.SIGNED and signed.tx_ref.startswith("0x")
        assert svc.wallet.balances[("USDC", "ethereum-sepolia")] == 490
        # Paid once, so they're no longer a new recipient
        again = await pay(svc, STRANGER, 10)
        assert again.decision.verdict == Verdict.ALLOW
    asyncio.run(main())


def test_policy_is_rechecked_at_signing_time():
    async def main():
        svc = make()
        a = await pay(svc, "alice", 40)
        assert a.decision.verdict == Verdict.ALLOW
        svc.update_limits(per_tx_limit_usd=30)
        result = await svc.approve(a.id)
        assert result.status == ApprovalStatus.BLOCKED
        assert svc.wallet.balances[("USDC", "ethereum-sepolia")] == 500
    asyncio.run(main())


def test_cannot_approve_twice_or_approve_blocked():
    async def main():
        svc = make()
        ok = await pay(svc, "alice", 5)
        await svc.approve(ok.id)
        with pytest.raises(PolicyError):
            await svc.approve(ok.id)
        blocked = await pay(svc, "bob", 500)
        with pytest.raises(PolicyError):
            await svc.approve(blocked.id)
    asyncio.run(main())
