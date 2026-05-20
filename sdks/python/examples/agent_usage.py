"""
Agent-style usage: multiple sequential run() calls in a single session.

This pattern matches a Claude Code-style agent flow where the agent
issues several tool-calls (shell commands) inside the same sandbox,
accumulating state across commands (files on disk, installed packages, etc.).

Run with:
    pip install opensandbox-azure
    python examples/agent_usage.py
"""

from __future__ import annotations

import sys

from opensandbox_azure import SandboxClient, RunResult
from opensandbox_azure.exceptions import SandboxError
from azure.identity import DefaultAzureCredential


def print_result(label: str, result: RunResult) -> None:
    print(f"\n── {label} (exit={result.exit_code}, {result.duration_ms}ms) ──")
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print("[stderr]", result.stderr, end="", file=sys.stderr)


def run_agent_workflow() -> None:
    client = SandboxClient(
        api_url="https://api-opensandbox.example.com",
        credential=DefaultAzureCredential(),
        scope="api://<api-app-id>/.default",
    )

    print("Creating session…")
    sess = client.create_session(
        image="acr.example.azurecr.io/sandbox/base/python:3.12",
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    print(f"Session {sess.session_id} ready (tier={sess.identity_tier.value})")

    try:
        # Step 1: Verify the runtime
        print_result("python version", sess.run("python --version"))

        # Step 2: Install a dependency inside the session
        print_result(
            "pip install",
            sess.run("pip install --quiet httpx", timeout_s=120),
        )

        # Step 3: Use the installed package
        print_result(
            "httpx version",
            sess.run("python -c 'import httpx; print(httpx.__version__)'"),
        )

        # Step 4: Write a file and read it back (state persists within the session)
        sess.run("echo 'hello from agent' > /tmp/agent_output.txt")
        print_result("read file", sess.run("cat /tmp/agent_output.txt"))

        # Step 5: Run a small computation
        script = (
            "import math; "
            "primes = [n for n in range(2, 50) if all(n % i for i in range(2, n))]; "
            "print('primes:', primes)"
        )
        print_result("compute primes", sess.run(f"python -c '{script}'"))

    except SandboxError as exc:
        print(f"Sandbox error (HTTP {exc.status_code}): {exc}", file=sys.stderr)
        raise
    finally:
        print(f"\nDeleting session {sess.session_id}…")
        sess.delete()
        print("Done.")


if __name__ == "__main__":
    run_agent_workflow()
