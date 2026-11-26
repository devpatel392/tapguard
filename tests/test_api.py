"""End-to-end through HTTP with the offline (scripted) agent and mock wallet."""
from fastapi.testclient import TestClient

from app.main import app


def test_chat_propose_tap_flow():
    with TestClient(app) as client:
        state = client.get("/api/state").json()
        assert state["wallet"] == "mock" and state["agent"] == "scripted"

        r = client.post("/api/chat", json={"message": "pay alice 20 usdc"}).json()
        assert r["reply"].startswith("Queued")
        pending = [a for a in client.get("/api/state").json()["approvals"] if a["status"] == "pending"]
        assert len(pending) == 1

        out = client.post(f"/api/approvals/{pending[0]['id']}/approve", json={}).json()
        assert out["status"] == "signed"

        r = client.post("/api/chat", json={"message": "approve unlimited usdc for coffee-shop"}).json()
        assert r["reply"].startswith("Blocked")

        r = client.post("/api/chat", json={"message": "send 40 USDC to 0x5EED123412341234123412341234123412341234"}).json()
        assert "warnings" in r["reply"]
        review_id = r["events"][0]["output"]["approval_id"]
        assert client.post(f"/api/approvals/{review_id}/approve", json={}).status_code == 400
        assert client.post(f"/api/approvals/{review_id}/approve", json={"acknowledge_warnings": True}).json()["status"] == "signed"

        limits = client.patch("/api/policy", json={"per_tx_limit_usd": 10}).json()
        assert limits["per_tx_limit_usd"] == 10
        assert client.patch("/api/policy", json={"daily_limit_usd": -1}).status_code == 400

        assert client.get("/").status_code == 200
