"""Settings from environment / .env. See .env.example."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    def __init__(self) -> None:
        env = os.getenv
        # mock = built-in fake wallet (offline). mcp = any MCP server (the fake one, or Tangem's).
        self.wallet_backend = env("WALLET_BACKEND", "mock").lower()
        self.mock_tap_delay = float(env("MOCK_TAP_DELAY", "1.2"))

        self.anthropic_api_key = env("ANTHROPIC_API_KEY", "")
        self.anthropic_model = env("ANTHROPIC_MODEL", "claude-sonnet-5-5")

        self.mcp_transport = env("MCP_TRANSPORT", "stdio").lower()  # stdio | http | sse
        self.mcp_command = env("MCP_COMMAND", "python")
        self.mcp_args = shlex.split(env("MCP_ARGS", "scripts/fake_tangem_mcp.py"))
        self.mcp_url = env("MCP_URL", "")
        self.mcp_auth_token = env("MCP_AUTH_TOKEN", "")
        self.mcp_tool_map = Path(env("MCP_TOOL_MAP", str(BASE_DIR / "mcp_tools.json")))

        self.policy_file = Path(env("POLICY_FILE", str(BASE_DIR / "policy.json")))


settings = Settings()
