# OpenSandbox Control Plane

FastAPI control plane for OpenSandbox on Azure. Handles sandbox session lifecycle, Entra JWT validation, OBO token exchange for AKS, and audit event emission.

---

## Local Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Access to an Azure AD tenant (for real Entra testing)

### Setup

```bash
cd apps/control-plane

# Create virtual environment and install all dependencies (including dev)
uv venv
uv pip install -e ".[dev]"
```

### Environment Variables

Copy and fill in the template below into a `.env` file:

```dotenv
# ── Entra / AAD ─────────────────────────────────────────────────────────────���
TENANT_ID=<your-tenant-id>
API_APP_ID=<opensandbox-api-app-registration-client-id>
API_APP_CLIENT_SECRET_KV_REF=<key-vault-secret-name-holding-client-secret>

# ── AKS ── IMPORTANT: must be the AAD-integrated AKS server app ID, NOT management.azure.com ──
AKS_SERVER_APP_ID=<aks-server-app-id-from-aadProfile.serverAppID>
AKS_API_FQDN=<private-aks-api-fqdn>
AKS_CA_BUNDLE_PATH=/etc/ssl/aks/ca.crt

# ── Key Vault ─────────────────────────────────────────────────────────────────
KEY_VAULT_URI=https://<your-kv-name>.vault.azure.net/

# ── Observability ─────────────────────────────────────────────────────────────
APPINSIGHTS_CONNECTION_STRING=InstrumentationKey=...
LOG_ANALYTICS_WORKSPACE_ID=<workspace-id>

# ── Sandbox runtime ───────────────────────────────────────────────────────────
ACR_FQDN=<your-acr>.azurecr.io
CURATED_IMAGES='["python:3.12", "node:20"]'

# ── Event Hubs (audit) ────────────────────────────────────────────────────────
EVENTHUB_CONNECTION_STRING=Endpoint=sb://...
EVENTHUB_NAME=sandbox-audit

# ── ARM provisioning ─────────────────��────────────────────────────────────────
ARM_SUBSCRIPTION_ID=<subscription-id>
ARM_RESOURCE_GROUP_USERS=rg-opensandbox-users
```

### Run locally

```bash
uvicorn app.main:app --reload --port 8000
```

OpenAPI docs: http://localhost:8000/docs  
Health: http://localhost:8000/healthz  
Readiness: http://localhost:8000/readyz

---

## Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=app --cov-report=term-missing

# Specific test file
pytest tests/test_jwt_validator.py -v
```

---

## Testing Against a Real Entra Tenant

1. Register an app in Entra ID:
   - Name: `opensandbox-api-dev`
   - Add an app role: `Sandbox.LowLatency`
   - Under "Expose an API", add scope: `Sandbox.Use`
   - Note the **Application (client) ID** → `API_APP_ID`

2. Get the AKS server app ID from your AKS cluster:
   ```bash
   az aks show -g <rg> -n <cluster> --query "aadProfile.serverAppId" -o tsv
   ```
   Set this as `AKS_SERVER_APP_ID`. **Do NOT use `management.azure.com`** (plan S-C8).

3. Acquire a token for your test user:
   ```bash
   az account get-access-token --resource <API_APP_ID> --query accessToken -o tsv
   ```

4. Call the API:
   ```bash
   TOKEN=$(az account get-access-token --resource <API_APP_ID> --query accessToken -o tsv)
   curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/sessions \
     -X POST -H "Content-Type: application/json" \
     -d '{"image": "python:3.12"}'
   ```

---

## Key Architecture Notes

- **OBO audience**: The control plane exchanges user tokens for AKS-scoped tokens. The OBO scope is `<AKS_SERVER_APP_ID>/.default` — never `management.azure.com`. This is enforced in `app/auth/obo_exchange.py` and tested in `tests/test_obo_exchange.py`.
- **Per-user isolation**: Each session runs in `ns-<user-oid>` with `runtimeClassName: kata-vm-isolation`.
- **Shared warm-pool tier**: Requires `Sandbox.LowLatency` role; audited with `identity_tier=shared_warm_pool`; rate-limited to 5/user + 100 platform-wide.
- **Audit**: Every `POST /sessions/{id}/run` emits a command-hash audit event to Event Hubs (not the raw command).

## Project Structure

```
app/
  main.py                  # FastAPI app + lifespan
  config.py                # Pydantic Settings
  exceptions.py            # Custom exception hierarchy
  aks_client.py            # kubernetes_asyncio wrapper (OBO-authenticated)
  auth/
    jwt_validator.py       # JWKS validation with 1-hour cache
    obo_exchange.py        # MSAL confidential-client OBO
    dependencies.py        # FastAPI auth dependency + UserClaims
  middleware/
    audit.py               # Structured logging + Event Hubs audit emission
  routers/
    sessions.py            # POST/GET/DELETE /sessions
    users.py               # POST /users/{oid}/provision
    healthz.py             # GET /healthz, GET /readyz
tests/
  test_jwt_validator.py    # JWKS cache, audience check, expiry tests
  test_obo_exchange.py     # OBO scope assertion (AKS server app, NOT management.azure.com)
  test_sessions_router.py  # 401/403 auth + ownership + image catalog tests
```
