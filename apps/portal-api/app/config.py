from __future__ import annotations

from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root resolver: this file is apps/portal-api/app/config.py, so the repo
# root is three parents up. This replaces the previous hardcoded Windows path
# so the portal is portable across dev machines.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_KEY_FILE = _REPO_ROOT / "examples" / ".opensandbox-api-key"


def _read_key_file() -> str:
    try:
        return _KEY_FILE.read_text().strip()
    except Exception:
        return ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Control plane (OpenSandbox server inside the cluster, via port-forward)
    CONTROL_PLANE_URL: str = "http://localhost:18080"
    CONTROL_PLANE_API_KEY: str = ""
    OPENSANDBOX_NAMESPACE: str = "opensandbox"

    # AKS cluster identity (used by /api/cluster/* and the identity banner)
    RESOURCE_GROUP: str = "rg-opensandbox-dev"
    CLUSTER_NAME: str = "aks-opensandbox-dev"

    # ── C1: image registry + default sandbox images (single source of truth) ──
    # Audit C-1 surfaced that ACR URI / VNC image / base sandbox image were
    # scattered across config.py, kata-pool.yaml, hypothesis_swarm.py and the
    # frontend defaults. Anyone rotating ACR had to edit three files.
    # The portal-api is the canonical source — the frontend pulls these via
    # GET /api/config. infra/* manifests still live in YAML (you can't
    # @computed_field a Kubernetes manifest), but the API + SDK paths agree.
    ACR_REGISTRY: str = "acropensandboxdemo7075.azurecr.io"
    SANDBOX_BASE_IMAGE_PATH: str = "python:3.12-slim"
    VNC_IMAGE_PATH: str = "opensandbox/desktop-vnc:latest"
    DEFAULT_POOL_NAME: str = "kata"

    # Kimi / Foundry (used by /api/kimi/chat)
    KIMI_ENDPOINT: str = (
        "https://aihubeastus26267492086.cognitiveservices.azure.com"
    )
    # Preferred order: K2.6 first, K2.5 as automatic fallback. The KimiClient
    # walks this tuple in order — if K2.6 ever 4xx/5xx, it transparently falls
    # back to K2.5 without the caller noticing.
    KIMI_DEPLOYMENTS: tuple[str, ...] = ("Kimi-K2.6", "Kimi-K2.5")
    KIMI_API_VERSION: str = "2024-10-21"

    # Swarm runner (used by /api/swarm/*)
    # The hypothesis swarm relies on a separate venv that has the opensandbox
    # SDK installed in editable mode. We point at its python.exe rather than
    # importing the SDK in-process — see the v2 plan for the rationale.
    SWARM_VENV_PYTHON: Path = (
        _REPO_ROOT / ".venv-swarm" / "Scripts" / "python.exe"
    )
    SWARM_SCRIPT: Path = _REPO_ROOT / "examples" / "hypothesis_swarm.py"

    # Repo root (exposed for clients that need to resolve relative paths)
    REPO_ROOT: Path = _REPO_ROOT

    # ── Computed image URIs ──

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SANDBOX_BASE_IMAGE(self) -> str:
        return f"{self.ACR_REGISTRY}/{self.SANDBOX_BASE_IMAGE_PATH}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def VNC_IMAGE(self) -> str:
        return f"{self.ACR_REGISTRY}/{self.VNC_IMAGE_PATH}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SWARM_DEFAULT_IMAGE(self) -> str:
        # Kept for backwards compatibility with main.py / swarm.py callers
        # that already reference SWARM_DEFAULT_IMAGE.
        return self.SANDBOX_BASE_IMAGE

    def model_post_init(self, __context: object) -> None:
        if not self.CONTROL_PLANE_API_KEY:
            object.__setattr__(self, "CONTROL_PLANE_API_KEY", _read_key_file())


settings = Settings()
