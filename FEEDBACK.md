# Tangem MCP feedback (fill in as you build; this is what earns the $40)

Write down problems the moment you hit them. "Took 10 minutes to work out X" beats "docs could be better".

## Setup and connection
- Transport / auth: Streamable HTTP at `https://mcp.tangem.org/mcp`, OAuth (QR code scanned with the Tangem app), no API keys. Tangem rejects dynamic client registration (DCR) — clients must supply a client metadata document (CIMD).
- Time from zero to first successful tool call: **blocked**. The documented `npx mcp-remote` path (v0.17.1) failed twice on Windows:
  1. `Existing OAuth client information is required when exchanging an authorization code` — worked around with `--static-oauth-client-info`.
  2. `Recursively reconnecting for reason: authentication-needed` — the token from a successful exchange is rejected with 401 on reconnect and `tools/list` never returns (mcp-remote loops).

## Tool design
- Tools that were missing for my use case: **no general "list wallet transfer history" tool** — only per-intent status (`transactions__get_transaction_status`), exchange history, and Tangem-Pay card history. A payments agent needs a recent-transfers list.
- Tool names or descriptions that confused me (or would confuse an LLM): `wallets__get_tokens` must be called first before `transactions__get_balances` (addresses/decimals/networkId come from it). Amount units differ per tool — `send_transaction` takes asset units ("0.5"), express + `get_balances` use minor units, Tangem Pay returns decimal strings — and you convert with `decimals` from `wallets__get_tokens`.
- Response shapes I had to normalise: (not reached — connection failed before tool calls succeeded)

## Signing and the tap
- What happens on the phone when a signing tool is called: there is **no signing tool**. `transactions__send_transaction` only "prepares a transfer and returns a deeplink" — the user signs by opening that deeplink in the Tangem app.
- Timeouts / what the agent sees while waiting for a tap: (not reached) — status is polled via `transactions__get_transaction_status` (CREATED/COMPLETED).
- What the tap screen shows: (not reached)

## Errors
- Error messages that were unhelpful (copy them exactly):
  - `Existing OAuth client information is required when exchanging an authorization code` (mcp-remote 0.17.1 — misleading; the real issue is client info isn't persisted across the OAuth legs).
  - `Recursively reconnecting for reason: authentication-needed` (mcp-remote 0.17.1 — token rejected on reconnect, no actionable detail).
  - `Incompatible auth server: does not support dynamic client registration` (when DCR is attempted; correct but the fix isn't obvious from the message).

## Mini-app platform
- How a mini-app is loaded / registered: (not attempted)
- What I'd want from a marketplace listing: (not attempted)

## One thing I'd fix first
- Make the MCP server connectable from a normal Python MCP client without `mcp-remote` — ship a working CIMD example (client metadata document + PKCE) that doesn't depend on a third-party Node proxy, and add a recent-transfers tool. The OAuth reconnect 401 is the single biggest blocker to actually using the server.
