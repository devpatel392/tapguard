"""Benchmark how well Claude models use an MCP server's tools, and lint the tool definitions.

    python -m evals.mcp_bench --lint-only                       # no API key: just review the tool schemas
    python -m evals.mcp_bench --models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5 --repeat 2

Connects using the MCP_* settings in .env (fake Tangem server by default). For each task it asks the
model to make ONE tool call and scores the tool + arguments. Tool calls are NEVER executed, so
nothing gets signed or sent, even against the real server.

Writes evals/results/mcp-bench-<stamp>.json and .md. The markdown is written to be handed to the
Tangem team as feedback.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.wallet.tangem_mcp import TangemMcpWallet  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
SYSTEM = (
    "You are an assistant for a Tangem crypto wallet. Use the available tools to do what the user asks. "
    "Make exactly one tool call. Do not ask follow-up questions."
)
WRITE_WORDS = re.compile(r"send|sign|transfer|approve|swap|pay|withdraw|broadcast", re.I)


# ---------- tool lint (no model needed) ----------
def _schema(tool) -> dict:
    return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}


def lint_tools(tools: list) -> list[dict]:
    issues = []

    def add(tool, severity, msg):
        issues.append({"tool": tool, "severity": severity, "issue": msg})

    for t in tools:
        desc = (t.description or "").strip()
        schema = _schema(t)
        props = schema.get("properties", {}) or {}
        ann = getattr(t, "annotations", None)
        if len(desc) < 25:
            add(t.name, "high", f"Description is {'missing' if not desc else 'very short'} ({len(desc)} chars). "
                                "Models choose tools mainly from this text.")
        is_write = bool(WRITE_WORDS.search(t.name))
        if is_write:
            if not (ann and getattr(ann, "destructive_hint", getattr(ann, "destructiveHint", None))):
                add(t.name, "medium", "Moves funds but has no destructiveHint annotation, so clients can't "
                                      "tell it apart from read-only tools.")
            if "tap" not in desc.lower() and "confirm" not in desc.lower():
                add(t.name, "low", "Description doesn't say the user must tap/confirm. Agents should know a "
                                   "call will block until the owner acts.")
        elif not (ann and getattr(ann, "read_only_hint", getattr(ann, "readOnlyHint", None))):
            add(t.name, "low", "Looks read-only but has no readOnlyHint annotation.")
        for name, p in props.items():
            if not p.get("description"):
                add(t.name, "medium", f"Parameter `{name}` has no description.")
            if name.lower() in {"chain", "network", "blockchain", "token", "symbol", "currency"} and not p.get("enum"):
                add(t.name, "medium", f"Parameter `{name}` is free text with no enum or examples, so models have "
                                      "to guess valid values (chain ids, token symbols).")
            if name.lower() in {"amount", "value"} and "decimal" not in (p.get("description") or "").lower() \
                    and p.get("type") in ("string", "integer"):
                add(t.name, "medium", f"`{name}` is a {p.get('type')}: say whether it's in whole tokens or "
                                      "smallest units (wei).")
        if props and not schema.get("required") and any("default" not in p for p in props.values()):
            add(t.name, "low", "No `required` list, so models can't tell which arguments are mandatory.")
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(issues, key=lambda i: (order[i["severity"]], i["tool"]))


# ---------- scoring ----------
def _eq(want: Any, got: Any) -> bool:
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        try:
            return abs(float(got) - float(want)) < 1e-9
        except (TypeError, ValueError):
            return False
    return str(want).strip().lower() == str(got).strip().lower()


def score_call(expect: dict, call: dict | None) -> tuple[str, str]:
    if call is None:
        return "no_tool_call", "model answered without calling a tool"
    if call["name"] != expect["tool"]:
        return "wrong_tool", f"called {call['name']}"
    bad = [f"{k}={call['input'].get(k)!r} (want {v!r})" for k, v in expect.get("args", {}).items()
           if not _eq(v, call["input"].get(k))]
    if bad:
        return "wrong_args", "; ".join(bad)
    return "correct", ""


async def ask(client, model: str, tools: list[dict], prompt: str) -> dict | None:
    resp = await client.messages.create(
        model=model, max_tokens=512, system=SYSTEM, tools=tools,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return {"name": block.name, "input": dict(block.input)}
    return None


# ---------- report ----------
def to_markdown(server_tools: list, lint: list[dict], runs: list[dict], models: list[str]) -> str:
    lines = ["# MCP tool-use benchmark", "", f"- **Server tools:** {', '.join(t.name for t in server_tools)}",
             f"- **Run at:** {datetime.now().isoformat(timespec='seconds')}", ""]
    if runs:
        lines += ["## Accuracy by model", "", "| Model | Correct | Wrong tool | Wrong args | No call | Errors |",
                  "|---|---|---|---|---|---|"]
        for m in models:
            c = Counter(r["result"] for r in runs if r["model"] == m)
            n = sum(c.values())
            lines.append(f"| {m} | {c['correct']}/{n} ({c['correct'] / n:.0%}) | {c['wrong_tool']} | "
                         f"{c['wrong_args']} | {c['no_tool_call']} | {c['error']} |")
        fails = [r for r in runs if r["result"] != "correct"]
        by_tool = Counter(r["expected_tool"] for r in fails)
        if fails:
            lines += ["", "## Where models struggled", "", "| Tool | Failures |", "|---|---|"]
            lines += [f"| {t} | {n} |" for t, n in by_tool.most_common()]
            lines += ["", "| Task | Model | Result | Detail |", "|---|---|---|---|"]
            lines += [f"| {r['task']} | {r['model']} | {r['result']} | {r['detail']} |" for r in fails]
        lines.append("")
    lines += ["## Tool definition review", ""]
    if not lint:
        lines.append("No issues found.")
    else:
        lines += ["| Severity | Tool | Issue |", "|---|---|---|"]
        lines += [f"| {i['severity']} | {i['tool']} | {i['issue']} |" for i in lint]
    return "\n".join(lines) + "\n"


async def main(argv: list[str] | None = None, client=None, wallet: TangemMcpWallet | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=settings.anthropic_model, help="comma-separated model ids")
    ap.add_argument("--tasks", default=str(HERE / "mcp_tasks.json"))
    ap.add_argument("--repeat", type=int, default=1, help="runs per task per model (models vary run to run)")
    ap.add_argument("--lint-only", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default=str(RESULTS_DIR))
    opts = ap.parse_args(argv)

    wallet = wallet or TangemMcpWallet.from_settings(settings)
    await wallet.start()
    try:
        server_tools = list(wallet.available.values())
    finally:
        await wallet.stop()  # we only needed the tool list; calls are never executed

    lint = lint_tools(server_tools)
    models = [m.strip() for m in opts.models.split(",") if m.strip()]
    runs: list[dict] = []

    if not opts.lint_only:
        if client is None:
            if not settings.anthropic_api_key:
                sys.exit("Benchmark needs ANTHROPIC_API_KEY in .env (or use --lint-only)")
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        tools = [{"name": t.name, "description": t.description or "", "input_schema": _schema(t)} for t in server_tools]
        tasks = json.loads(Path(opts.tasks).read_text())["tasks"]
        sem = asyncio.Semaphore(opts.concurrency)

        async def one(model, task, rep):
            async with sem:
                try:
                    call = await ask(client, model, tools, task["prompt"])
                    result, detail = score_call(task["expect"], call)
                except Exception as e:  # noqa: BLE001
                    call, result, detail = None, "error", str(e)[:200]
                return {"model": model, "task": task["id"], "rep": rep, "expected_tool": task["expect"]["tool"],
                        "call": call, "result": result, "detail": detail}

        runs = await asyncio.gather(*(one(m, t, r) for m in models for t in tasks for r in range(opts.repeat)))

    md = to_markdown(server_tools, lint, runs, models)
    out = Path(opts.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / f"mcp-bench-{stamp}.json").write_text(json.dumps({"lint": lint, "runs": runs}, indent=2, default=str))
    (out / f"mcp-bench-{stamp}.md").write_text(md)
    print(md)
    print(f"Report: {out / f'mcp-bench-{stamp}.md'}")
    by_model = defaultdict(list)
    for r in runs:
        by_model[r["model"]].append(r["result"] == "correct")
    return {"lint": lint, "runs": runs, "accuracy": {m: sum(v) / len(v) for m, v in by_model.items()}}


if __name__ == "__main__":
    asyncio.run(main())
