# Claude Code prompts, in order

Open a terminal in this folder and run `claude`. Paste one prompt at a time, and check the result before moving on.

## Before the night (at home)

**1. Setup check**
> Create a venv, install requirements.txt, copy .env.example to .env, run pytest and the policy-mode red team. Tell me the headline numbers. Don't change any code.

**2. Real-agent eval (needs ANTHROPIC_API_KEY in .env)**
> Run the red team in llm mode with claude-sonnet-5-5, then again with --no-hardening. Compare the two reports in a short table: attacks stopped, unsafe actions the agent attempted, and normal requests that worked. List any scenario where the agent attempted something unsafe, and what it did.

**3. Fix what the eval found**
> Look at the failed and "warned" scenarios in the latest llm report. Propose the smallest change that fixes each one without breaking normal requests. Follow the hard rules in CLAUDE.md. Show me the plan before editing.

**4. Model comparison**
> Run mcp_bench against the fake server with claude-haiku-5-5, claude-sonnet-5-5 and claude-opus-5-5, repeat 2. Summarise accuracy per model and the tasks they got wrong.

**5. Add your own attacks**
> Add 5 new attack scenarios to evals/scenarios.py that test new ideas (for example an injection in a contact name, or a fake "transaction failed, retry to this address" memo). Run policy and llm mode and tell me what changed.

## On the night (6:00–7:30)

**6. Connect to Tangem's MCP** (after you've put their connection details in .env)
> Run scripts/inspect_mcp.py and list Tangem's tools with their parameters. Map them to the three operations in mcp_tools.json (get_balances, get_history, sign_and_send). Show me the proposed mcp_tools.json before saving. Only call read-only tools.

**7. Make the responses parse**
> Call Tangem's read-only balance and history tools via inspect_mcp.py --call. If the response shapes don't match _normalise_balances / _normalise_history / _tx_ref in app/wallet/tangem_mcp.py, update those functions and add a unit test with the real response shape.

**8. Update the policy for their chains**
> Update policy.json allowed_chains and allowed_tokens to match the identifiers Tangem's MCP uses, then run pytest and the policy red team.

**9. Benchmark their MCP (the feedback that earns the $40)**
> Rewrite evals/mcp_tasks.json for Tangem's real tool names and arguments (same kinds of tasks). Run mcp_bench --lint-only, then the benchmark with two models. Then write evals/results/TANGEM_FEEDBACK.md: one page, the top 3 tool-definition fixes with suggested wording, accuracy numbers, and anything confusing about setup. Write it plainly, like a developer, not marketing.

**10. End-to-end with the card**
> Set WALLET_BACKEND=mcp and start the app. Walk me through a tiny testnet payment end to end, and watch the server log for errors while I tap the card.

## 7:30: stop building

**11. Demo prep**
> Give me a 2-minute demo script based on the latest red-team and benchmark numbers: problem, live tap, one blocked injection, the headline numbers, and what's next.
