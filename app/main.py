"""HTTP API + static UI.  Run:  uvicorn app.main:app --reload"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import make_agent
from .config import BASE_DIR, settings
from .policy import Policy
from .service import PolicyError, TapGuardService
from .wallet import make_wallet

STATIC = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    wallet = make_wallet(settings)
    await wallet.start()
    service = TapGuardService(wallet, Policy.load(settings.policy_file))
    agent = make_agent(service, settings)
    app.state.service, app.state.agent = service, agent
    service.store.log("startup", f"Wallet: {wallet.name}. Agent: {agent.name}.")
    try:
        yield
    finally:
        await wallet.stop()


app = FastAPI(title="TapGuard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ApproveIn(BaseModel):
    acknowledge_warnings: bool = False


class LimitsIn(BaseModel):
    per_tx_limit_usd: float | None = None
    daily_limit_usd: float | None = None
    review_above_usd: float | None = None


def svc() -> TapGuardService:
    return app.state.service


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
async def get_state():
    return {**await svc().state(), "agent": app.state.agent.name}


@app.post("/api/chat")
async def chat(body: ChatIn):
    try:
        return await app.state.agent.chat(body.message.strip())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Agent error: {e}") from e


@app.post("/api/chat/reset")
async def reset_chat():
    app.state.agent.reset()
    return {"ok": True}


@app.post("/api/approvals/{approval_id}/approve")
async def approve(approval_id: str, body: ApproveIn | None = None):
    try:
        a = await svc().approve(approval_id, acknowledge_warnings=bool(body and body.acknowledge_warnings))
    except PolicyError as e:
        raise HTTPException(400, str(e)) from e
    return a.model_dump(mode="json")


@app.post("/api/approvals/{approval_id}/reject")
async def reject(approval_id: str):
    try:
        return svc().reject(approval_id).model_dump(mode="json")
    except PolicyError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/api/policy")
async def update_policy(body: LimitsIn):
    try:
        svc().update_limits(**body.model_dump())
    except PolicyError as e:
        raise HTTPException(400, str(e)) from e
    return (await svc().state())["limits"]
