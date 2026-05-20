"""
agent.py — Kimi K2.5 + OpenSandbox ACI agent demo.

Usage:
    python agent.py "Your task here"
    python agent.py --task-file tasks/data-analysis.txt

Environment (optional overrides):
    KIMI_ENDPOINT       — defaults to the Azure AI Foundry endpoint below
    AZURE_SUBSCRIPTION_ID
    SANDBOX_RESOURCE_GROUP
    SANDBOX_LOCATION
    ACR_LOGIN_SERVER
    SANDBOX_IMAGE
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from aci_sandbox import ACISandbox, RunResult, Session
from kimi_client import KimiClient

# ---------------------------------------------------------------------------
# Configuration (all overridable via env)
# ---------------------------------------------------------------------------

KIMI_ENDPOINT = os.getenv(
    "KIMI_ENDPOINT",
    "https://aihubeastus26267492086.services.ai.azure.com/models/chat/completions"
    "?api-version=2024-05-01-preview",
)
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID", "b914f690-dab0-4208-98af-c7ee89ab9040")
RESOURCE_GROUP = os.getenv("SANDBOX_RESOURCE_GROUP", "rg-opensandbox-demo")
LOCATION = os.getenv("SANDBOX_LOCATION", "eastus")
ACR_LOGIN_SERVER = os.getenv("ACR_LOGIN_SERVER", "acropensandboxdemo7075.azurecr.io")
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "sandbox/base/python:3.12")

EVIDENCE_DIR = Path(__file__).parent / "evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)

console = Console(highlight=True)
logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Tool definitions sent to Kimi
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_sandbox",
            "description": (
                "Create a fresh, isolated Linux sandbox container on Azure Container Instances. "
                "Call this before running any commands. Returns the session_id to use with "
                "run_in_sandbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why you need a new sandbox",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_in_sandbox",
            "description": (
                "Run a shell command inside the active sandbox. "
                "Multiple commands that share state should be composed with && or ; "
                "since each call is a fresh bash invocation. "
                "Returns stdout/stderr output and exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you're running this command",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_sandbox",
            "description": "Destroy the current sandbox. Always call this when done to avoid Azure costs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why you're deleting the sandbox",
                    }
                },
            },
        },
    },
]

SYSTEM_PROMPT = """You are a careful AI engineer with access to an isolated Linux sandbox running on Azure Container Instances.

Available tools:
- create_sandbox: Create a fresh isolated sandbox before doing any work
- run_in_sandbox: Execute shell commands in the sandbox
- delete_sandbox: Destroy the sandbox when done (ALWAYS call this at the end)

Guidelines:
1. Always call create_sandbox first before any run_in_sandbox calls.
2. Think step-by-step about what you need to do before issuing commands.
3. Compose multi-step operations into single commands where state needs to persist (e.g. python -c "..." && cat /tmp/result.txt).
4. Always call delete_sandbox at the end to clean up Azure resources.
5. Report your findings clearly after deleting the sandbox.
"""

# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def dispatch_tool(
    name: str,
    args: dict[str, Any],
    sandbox: ACISandbox,
    state: dict[str, Any],
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute a tool call and return a JSON-serialisable result."""
    t0 = time.monotonic()

    if name == "create_sandbox":
        reason = args.get("reason", "")
        console.print(
            Panel(
                f"[bold green]create_sandbox[/bold green]\n[dim]{reason}[/dim]",
                title="🔧 Tool Call",
                border_style="green",
            )
        )
        try:
            session: Session = sandbox.create_session()
            state["active_session_id"] = session.session_id
            result = {
                "status": "ok",
                "session_id": session.session_id,
                "container_group": session.container_group_name,
                "message": f"Sandbox created. session_id={session.session_id}",
            }
            console.print(f"  [green]✓[/green] Session [bold]{session.session_id[:12]}…[/bold] running")
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "message": str(exc)}
            console.print(f"  [red]✗[/red] {exc}")

    elif name == "run_in_sandbox":
        command: str = args["command"]
        reason = args.get("reason", "")
        session_id = state.get("active_session_id")
        if not session_id:
            result = {"status": "error", "message": "No active session. Call create_sandbox first."}
        else:
            console.print(
                Panel(
                    f"[bold yellow]run_in_sandbox[/bold yellow]\n"
                    f"[dim]{reason}[/dim]\n\n"
                    f"[cyan]$ {command}[/cyan]",
                    title="🔧 Tool Call",
                    border_style="yellow",
                )
            )
            try:
                run_result: RunResult = sandbox.run(session_id, command)
                result = {
                    "status": "ok",
                    "output": run_result.output,
                    "exit_code": run_result.exit_code,
                    "duration_s": round(run_result.duration_s, 2),
                }
                output_preview = run_result.output[:500] + ("…" if len(run_result.output) > 500 else "")
                console.print(
                    Panel(
                        f"[dim]exit={run_result.exit_code}  ({run_result.duration_s:.1f}s)[/dim]\n\n"
                        f"{output_preview}",
                        title="📤 Tool Result",
                        border_style="blue",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "message": str(exc)}
                console.print(f"  [red]✗[/red] {exc}")

    elif name == "delete_sandbox":
        reason = args.get("reason", "")
        session_id = state.get("active_session_id")
        console.print(
            Panel(
                f"[bold red]delete_sandbox[/bold red]\n[dim]{reason}[/dim]",
                title="🔧 Tool Call",
                border_style="red",
            )
        )
        if not session_id:
            result = {"status": "ok", "message": "No active session to delete."}
        else:
            try:
                sandbox.delete(session_id)
                state["active_session_id"] = None
                result = {"status": "ok", "message": f"Session {session_id[:12]} deleted."}
                console.print(f"  [green]✓[/green] Sandbox deleted")
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "message": str(exc)}
                console.print(f"  [red]✗[/red] {exc}")
    else:
        result = {"status": "error", "message": f"Unknown tool: {name}"}

    elapsed = time.monotonic() - t0
    trace.append({
        "type": "tool_call",
        "tool": name,
        "args": args,
        "result": result,
        "wall_clock_s": round(elapsed, 3),
        "ts": datetime.utcnow().isoformat(),
    })
    return result


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------


def run_agent(task: str) -> None:
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    trace_path = EVIDENCE_DIR / f"kimi-agent-trace-{timestamp}.json"

    console.print(Rule("[bold blue]Kimi K2.5 + OpenSandbox ACI Agent[/bold blue]"))
    console.print(Panel(task, title="📋 Task", border_style="blue"))

    credential = DefaultAzureCredential()
    kimi = KimiClient(endpoint=KIMI_ENDPOINT, credential=credential)
    sandbox = ACISandbox(
        credential=credential,
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        location=LOCATION,
        acr_login_server=ACR_LOGIN_SERVER,
        sandbox_image=SANDBOX_IMAGE,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    state: dict[str, Any] = {"active_session_id": None}
    trace: list[dict[str, Any]] = []
    max_turns = 12

    try:
        for turn in range(1, max_turns + 1):
            console.print(Rule(f"[dim]Turn {turn}/{max_turns}[/dim]"))

            t0 = time.monotonic()
            resp = kimi.chat(messages, tools=TOOLS, max_tokens=2048, temperature=0.0)
            kimi_latency = time.monotonic() - t0

            choice = resp["choices"][0]
            msg = choice["message"]
            finish_reason = choice.get("finish_reason", "")

            # Show reasoning content if present (Kimi reasoning model)
            reasoning = msg.get("reasoning_content", "")
            if reasoning:
                preview = reasoning[:400] + ("…" if len(reasoning) > 400 else "")
                console.print(
                    Panel(
                        f"[dim italic]{preview}[/dim italic]",
                        title=f"🧠 Kimi Reasoning  ({kimi_latency:.1f}s)",
                        border_style="magenta",
                    )
                )

            # Record in trace
            trace.append({
                "type": "llm_response",
                "turn": turn,
                "finish_reason": finish_reason,
                "reasoning_preview": reasoning[:200] if reasoning else None,
                "tool_calls": [
                    {"id": tc["id"], "name": tc["function"]["name"], "args": tc["function"]["arguments"]}
                    for tc in (msg.get("tool_calls") or [])
                ],
                "content": msg.get("content"),
                "latency_s": round(kimi_latency, 3),
                "ts": datetime.utcnow().isoformat(),
            })

            messages.append(msg)

            if finish_reason == "tool_calls":
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    result = dispatch_tool(name, args, sandbox, state, trace)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result),
                    })

            else:
                # Final answer
                final = msg.get("content") or ""
                console.print(Rule("[bold green]Final Answer[/bold green]"))
                console.print(Panel(final, title="✅ Kimi K2.5 Answer", border_style="green"))
                break

        else:
            console.print("[red]Max turns reached — agent did not finish.[/red]")

    finally:
        # Ensure sandbox is cleaned up even on error
        if state.get("active_session_id"):
            console.print("[yellow]Safety cleanup: deleting active sandbox …[/yellow]")
            try:
                sandbox.delete(state["active_session_id"])
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Cleanup error: {exc}[/red]")

        # Save evidence trace
        evidence = {
            "task": task,
            "timestamp": timestamp,
            "kimi_endpoint": KIMI_ENDPOINT,
            "subscription_id": SUBSCRIPTION_ID,
            "resource_group": RESOURCE_GROUP,
            "messages": messages,
            "trace": trace,
        }
        trace_path.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        console.print(f"\n[dim]Evidence trace saved → {trace_path}[/dim]")

        kimi.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Kimi K2.5 + OpenSandbox ACI agent demo")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("task", nargs="?", help="Task string to give to the agent")
    group.add_argument("--task-file", type=Path, help="Path to a .txt file containing the task")
    args = parser.parse_args()

    if args.task_file:
        task = args.task_file.read_text(encoding="utf-8").strip()
    elif args.task:
        task = args.task
    else:
        task = (
            "Calculate the first 20 prime numbers using Python, then save the result to "
            "/tmp/primes.txt inside the sandbox, then read the file back and report the "
            "SHA256 of its contents. Use the sandbox for everything."
        )

    run_agent(task)


if __name__ == "__main__":
    main()
