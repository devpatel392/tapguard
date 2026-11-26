"""Native OAuth to Tangem MCP (no mcp-remote / npx).

Tangem uses the OAuth authorization-code flow with PKCE and a client-metadata
document as the client_id. The token must be bound to the MCP resource via the
`resource` parameter in BOTH the authorize URL and the token exchange, or the
MCP server rejects it with 403 invalid_token. Tangem's protected-resource
metadata endpoint returns an empty body, so the MCP SDK's automatic OAuth
discovery can't be used: we drive the flow by hand here.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import truststore

truststore.inject_into_ssl()  # use the Windows cert store (the machine's CA bundle is missing a root)

TANGEM_SERVER_URL = "https://mcp.tangem.org/mcp"
CLIENT_ID = "https://opencode.ai/oauth/opencode/client.json"
AUTHORIZE_ENDPOINT = "https://authentication.tangem.org/api/v1/mcp/authorize"
TOKEN_ENDPOINT = "https://authentication.tangem.org/api/v1/mcp/token"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 21351
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"
TOKEN_FILE = Path.home() / ".tangem_mcp_token.json"


def _generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _load_token() -> str | None:
    try:
        return json.loads(TOKEN_FILE.read_text()).get("access_token")
    except Exception:
        return None


def _save_token(token: str) -> None:
    TOKEN_FILE.write_text(json.dumps({"access_token": token}))


class _CallbackServer:
    """Minimal asyncio HTTP server that captures the OAuth redirect."""

    def __init__(self):
        self._server: asyncio.Server | None = None
        self._result: asyncio.Future | None = None

    async def start(self) -> None:
        self._result = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._handle, CALLBACK_HOST, CALLBACK_PORT)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def wait_for_code(self) -> dict:
        assert self._result is not None
        return await self._result

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=60)
            path = request_line.decode("latin1", "replace").split(" ")[1]
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=60)
                if line in (b"\r\n", b"\n", b""):
                    break
            if urlparse(path).path == "/callback":
                qs = parse_qs(urlparse(path).query)
                if self._result is not None and not self._result.done():
                    self._result.set_result({
                        "code": qs.get("code", [None])[0],
                        "state": qs.get("state", [None])[0],
                    })
                body = b"Authorization complete. You can close this tab."
                status = b"HTTP/1.1 200 OK"
            else:
                body = b"Not found"
                status = b"HTTP/1.1 404 Not Found"
            writer.write(status + b"\r\nContent-Type: text/plain\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()


async def get_tangem_token() -> str:
    """Return a valid access token, reusing a cached one or doing the OAuth flow.

    The OAuth flow opens the sign-in page in the browser and blocks until the
    user scans the QR with the Tangem app and approves. The token is cached so
    later runs don't need another scan.
    """
    token = _load_token()
    if token:
        return token

    cb = _CallbackServer()
    await cb.start()
    try:
        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(32)
        auth_url = AUTHORIZE_ENDPOINT + "?" + urlencode({
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "scope": "",
            "resource": TANGEM_SERVER_URL,
        })
        print(f"[tapguard] Sign in to Tangem:\n{auth_url}\n", flush=True)
        await asyncio.to_thread(webbrowser.open, auth_url)
        result = await cb.wait_for_code()
    finally:
        await cb.stop()

    if result["state"] != state:
        raise RuntimeError("OAuth state mismatch")

    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.post(TOKEN_ENDPOINT, data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": code_verifier,
            "resource": TANGEM_SERVER_URL,
        })
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: HTTP {resp.status_code}\n{resp.text}")
    token = resp.json()["access_token"]
    _save_token(token)
    return token
