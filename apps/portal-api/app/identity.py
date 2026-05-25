from __future__ import annotations

import subprocess
from typing import Any

from .config import settings


def _run(cmd: list[str]) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except Exception:
        return None


def resolve_identity() -> dict[str, Any]:
    az_user = _run(["az", "account", "show", "--query", "user.name", "-o", "tsv"])
    az_subscription_id = _run(["az", "account", "show", "--query", "id", "-o", "tsv"])
    az_subscription_name = _run(["az", "account", "show", "--query", "name", "-o", "tsv"])
    kubectx = _run(["kubectl", "config", "current-context"])

    key_file = settings.REPO_ROOT / "examples" / ".opensandbox-api-key"
    try:
        key_file_exists = key_file.exists()
    except Exception:
        key_file_exists = False

    return {
        "az_user": az_user,
        "az_subscription_id": az_subscription_id,
        "az_subscription_name": az_subscription_name,
        "kubectx": kubectx,
        "cluster_namespace": settings.OPENSANDBOX_NAMESPACE,
        "key_file_exists": key_file_exists,
        "repo_root": str(settings.REPO_ROOT),
    }
