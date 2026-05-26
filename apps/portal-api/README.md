# DarkForge Portal API

FastAPI backend that joins the OpenSandbox control-plane with live Kubernetes data and serves the frontend.

## Security

> **⚠️ DEV ONLY — runs as YOU.** This portal uses the developer's local `az` session, kubeconfig, and API key file. It can start/stop the cluster, create/delete sandboxes, and call Kimi as the signed-in user. Do not expose it beyond `localhost` without reading [`docs/PORTAL-AUTH.md`](../../docs/PORTAL-AUTH.md).

## Prerequisites

- Python 3.12+
- `uv` installed
- `kubectl` configured with a valid kubeconfig pointing at your AKS cluster
- Port-forward to the in-cluster control plane:
  ```
  kubectl port-forward -n opensandbox-system svc/opensandbox-server 18080:80
  ```

## Run

```bash
cd apps/portal-api
uv sync
uv run uvicorn app.main:app --reload --port 8090
```

Open http://localhost:8090 for the dashboard.

## Environment

| Variable | Default | Description |
|---|---|---|
| `CONTROL_PLANE_URL` | `http://localhost:18080` | Base URL of the OpenSandbox control plane |
| `CONTROL_PLANE_API_KEY` | *(from key file)* | API key; falls back to reading the local key file |
| `OPENSANDBOX_NAMESPACE` | `opensandbox` | Kubernetes namespace for sandbox pods (the control-plane itself lives in `opensandbox-system`) |
| `RESOURCE_GROUP` | `rg-opensandbox-dev` | AKS resource group for `/api/cluster/*` |
| `CLUSTER_NAME` | `aks-opensandbox-dev` | AKS cluster name for `/api/cluster/*` |
| `ACR_REGISTRY` | `acropensandboxdemo7075.azurecr.io` | Image registry (C1 single source of truth). `SANDBOX_BASE_IMAGE` and `VNC_IMAGE` are computed from this. |
| `SANDBOX_BASE_IMAGE_PATH` | `python:3.12-slim` | Path under `ACR_REGISTRY` for the default Python sandbox |
| `VNC_IMAGE_PATH` | `opensandbox/desktop-vnc:latest` | Path under `ACR_REGISTRY` for the desktop/VNC sandbox |
| `DEFAULT_POOL_NAME` | `kata` | Pool CR name surfaced in `/api/config` |
| `KIMI_ENDPOINT` | `https://aihubeastus26267492086.cognitiveservices.azure.com` | Foundry endpoint for `/api/kimi/chat` |
| `KIMI_DEPLOYMENTS` | `("Kimi-K2.6","Kimi-K2.5")` | Deployment fallback chain |
| `KIMI_API_VERSION` | `2024-10-21` | Foundry API version |

Live values are exposed at `GET /api/config`. The frontend reads from there
instead of hardcoding its own copies (audit finding C-1).

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET    | `/api/health` | Liveness check |
| GET    | `/api/config` | Resolved config (ACR, default images, pool name) for the frontend |
| GET    | `/api/identity` | Resolved az user, subscription, kubectx, namespace, key file presence |
| GET    | `/api/cluster/state` | AKS provisioning + power state (2 s cached). Includes `last_action`, `last_action_at`, `last_actor`, `last_outcome`, `last_duration_s` (P0-6). |
| GET    | `/api/cluster/summary` | Node and pod counts by pool |
| POST   | `/api/cluster/start` | `az aks start --no-wait` — also records start in cluster-history.json |
| POST   | `/api/cluster/stop` | `az aks stop --no-wait` — also records stop in cluster-history.json |
| GET    | `/api/sandboxes` | List sandboxes with pod/node enrichment |
| POST   | `/api/sandboxes` | Proxy create-sandbox to the control plane |
| DELETE | `/api/sandboxes/{id}` | Proxy delete-sandbox to the control plane |
| POST   | `/api/sandbox/exec` | Run a Python snippet in a fresh Kata VM; auto-captures matplotlib charts as base64 PNG. Spawns `.venv-swarm/Scripts/python.exe examples/run_in_sandbox.py`. |
| POST   | `/api/sandbox/vnc` | Create a desktop/VNC sandbox (#18); returns `vnc_url` the UI can iframe |
| GET    | `/api/pool/{name}` | Normalized Pool CR (`total/allocated/available/poolMax/bufferMin`) |
| PATCH  | `/api/pool/{name}` | Patch `poolMin/poolMax/bufferMin/bufferMax` on the Pool CR (#19) |
| GET    | `/api/events` | Recent namespace events with `severity_class`, `human_message`, `is_for_deleted_sandbox` (P0-5) |
| GET    | `/api/history/chat` | Persisted chat messages, filtered by conversation_id (C3) |
| GET    | `/api/history/chat/conversations` | Conversation list with last_ts + message_count (C3) |
| GET    | `/api/history/swarm` | Recent swarm runs from SQLite (C3) |
| GET    | `/api/history/sandbox` | Sandbox creation/expiry log (C3) |
| POST   | `/api/swarm/runs` | Kick off `examples/hypothesis_swarm.py` |
| GET    | `/api/swarm/runs` | List active + recent runs |
| GET    | `/api/swarm/runs/{id}/events` | SSE stream (phase, result, summary, log, done) |
| DELETE | `/api/swarm/runs/{id}` | Cancel a run |
| POST   | `/api/kimi/chat` | Foundry chat-completions proxy (K2.6 default, K2.5 auto-fallback). Accepts optional `conversation_id` to persist a thread (C3). |
| GET    | `/` | Serves frontend `index.html` |

> For DEV-MODE vs prod-mode auth, see [`../../docs/PORTAL-AUTH.md`](../../docs/PORTAL-AUTH.md).
