"""Application configuration via Pydantic Settings (env vars)."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Entra / AAD ──────────────────────────────────────────────────────────
    tenant_id: str = Field(..., description="Azure AD tenant ID")
    api_app_id: str = Field(..., description="OpenSandbox API app registration client ID")
    api_app_client_secret_kv_ref: str = Field(
        ...,
        description="Key Vault secret name holding the API app client secret",
    )

    # ── AKS ──────────────────────────────────────────────────────────────────
    aks_server_app_id: str = Field(
        ...,
        description=(
            "AAD-integrated AKS server app ID (aadProfile.serverAppID). "
            "OBO exchange target — NOT management.azure.com. See plan S-C8 / B1."
        ),
    )
    aks_api_fqdn: str = Field(..., description="Private FQDN of the AKS API server")
    aks_ca_bundle_path: str = Field(
        "/etc/ssl/aks/ca.crt",
        description="Path to the AKS cluster CA certificate bundle",
    )

    # ── Key Vault ─────────────────────────────────────────────────────────────
    key_vault_uri: str = Field(..., description="Azure Key Vault URI, e.g. https://kv-xxx.vault.azure.net/")

    # ── Observability ─────────────────────────────────────────────────────────
    log_analytics_workspace_id: str = Field("", description="Log Analytics workspace ID")
    appinsights_connection_string: str = Field(
        "", description="Application Insights connection string"
    )

    # ── Sandbox runtime ───────────────────────────────────────────────────────
    warm_pool_namespace_prefix: str = Field(
        "ns-warm-pool",
        description="Namespace prefix for warm-pool pods",
    )
    warm_pool_rate_limit_per_user: int = Field(
        5,
        description="Max concurrent shared-tier sessions per user",
    )
    warm_pool_platform_limit: int = Field(
        100,
        description="Max concurrent shared-tier sessions platform-wide",
    )
    sandbox_namespace_prefix: str = Field("ns", description="Prefix for per-user sandbox namespaces")
    acr_fqdn: str = Field(..., description="ACR FQDN used for image allowlist, e.g. acropensandbox.azurecr.io")
    curated_images: list[str] = Field(
        default_factory=list,
        description="Curated image tags allowed in /sessions (without registry prefix)",
    )

    # ── Event Hubs (audit) ────────────────────────────────────────────────────
    eventhub_connection_string: str = Field(
        "",
        description="Event Hubs connection string for audit events",
    )
    eventhub_name: str = Field("sandbox-audit", description="Event Hub name for audit events")

    # ── ARM (user provisioning) ───────────────────────────────────────────────
    arm_subscription_id: str = Field("", description="Azure subscription ID")
    arm_resource_group_users: str = Field(
        "rg-opensandbox-users",
        description="Resource group where per-user resources are provisioned",
    )

    # ── JWKS cache ────────────────────────────────────────────────────────────
    jwks_cache_ttl_seconds: int = Field(3600, description="JWKS cache TTL in seconds")

    @field_validator("key_vault_uri")
    @classmethod
    def _normalize_kv_uri(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def aks_obo_scope(self) -> str:
        """OBO scope for AKS — MUST be AKS server app, not management.azure.com."""
        return f"{self.aks_server_app_id}/.default"

    @property
    def jwks_uri(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def issuer(self) -> str:
        return f"https://sts.windows.net/{self.tenant_id}/"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
