# Kimi K2.5 + OpenSandbox ACI Agent Demo

A live agentic application that uses **Kimi K2.5** (deployed on Azure AI Foundry) as the reasoning brain and **Azure Container Instances** as isolated code-execution sandboxes.

```
User task → Kimi K2.5 (tool calls) → ACI sandbox (real Azure infra) → evidence trace
```

## Architecture

```
agent.py
  ├── KimiClient      — Kimi K2.5 chat-completions (Bearer token via DefaultAzureCredential)
  ├── ACISandbox      — ACI container lifecycle + exec (azure-mgmt-containerinstance)
  └── evidence/       — JSON trace per run (messages + ACI wall-clock times)
```

Each sandbox session = one ACI container group running `sandbox/base/python:3.12`.  
Commands run via the ACI exec WebSocket API — each call is a fresh bash shell.

## Prerequisites

- Python ≥ 3.11
- [uv](https://github.com/astral-sh/uv) (or pip)
- Azure CLI (`az`) logged in: `az login`
- Contributor access to `rg-opensandbox-demo` (subscription `b914f690-dab0-4208-98af-c7ee89ab9040`)

## Quick start

```bash
# 1. Install dependencies
uv venv && uv pip install -e .

# 2. Build & push the sandbox image (no local Docker required — uses az acr build)
./sandbox_image/build-and-push.sh

# 3. Run the agent with a task string
python agent.py "Calculate the first 20 prime numbers, save to /tmp/primes.txt, and report the SHA256."

# 4. Or load a task from file
python agent.py --task-file tasks/data-analysis.txt
```

## Sample tasks

| File | Description |
|------|-------------|
| `tasks/data-analysis.txt` | Generate a random temperature dataset and find statistical outliers |
| `tasks/sandbox-isolation-test.txt` | Verify sandbox isolation (passwd, shadow, env vars) |
| `tasks/multi-step-coding.txt` | Write + run two Python scripts in sequence, verify Fibonacci property |

## Evidence traces

Every run saves a full trace to `evidence/kimi-agent-trace-<timestamp>.json`:

```json
{
  "task": "...",
  "timestamp": "20260520T143012",
  "messages": [...],        // full OpenAI-format message list
  "trace": [
    {"type": "llm_response", "turn": 1, "latency_s": 2.3, ...},
    {"type": "tool_call", "tool": "create_sandbox", "wall_clock_s": 18.4, ...},
    ...
  ]
}
```

## Configuration

All config can be overridden via environment variables:

| Variable | Default |
|----------|---------|
| `KIMI_ENDPOINT` | Azure AI Foundry endpoint |
| `AZURE_SUBSCRIPTION_ID` | `b914f690-...` |
| `SANDBOX_RESOURCE_GROUP` | `rg-opensandbox-demo` |
| `SANDBOX_LOCATION` | `eastus` |
| `ACR_LOGIN_SERVER` | `acropensandboxdemo7075.azurecr.io` |
| `SANDBOX_IMAGE` | `sandbox/base/python:3.12` |

## Notes

- **No Docker required** — `sandbox_image/build-and-push.sh` uses `az acr build` (cloud build in ACR Tasks).
- **No hardcoded secrets** �� `DefaultAzureCredential` handles Managed Identity / az CLI auth.
- **ACI exec design** — Each `run_in_sandbox` call opens a transient bash shell via the ACI WebSocket exec API. Commands needing shared state should be composed with `&&` or `;`.
- **Kimi reasoning** — The model's `reasoning_content` field is displayed (truncated) in the terminal per turn.
