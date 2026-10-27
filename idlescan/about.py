"""``idlescan about``: safe, paste-able diagnostics for support.

The output is deliberately narrow.  It never includes RPC URLs, API keys,
webhook URLs, environment values, wallet addresses or absolute paths — only
booleans, counts, versions and the database filename.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .chains import load_chains
from .config import Settings
from .db import Database

LICENSE_NAME = "Proprietary single-user license (see LICENSE)"
PRODUCT_NAME = "idlescan Desk"


def _database_filename(path: Path) -> str:
    """Relative paths may be shown as-is; absolute paths reveal only the file."""
    return path.name if path.is_absolute() else str(path)


def _schema_state(db_path: Path) -> str:
    if not db_path.exists():
        return "not created"
    try:
        version = Database(db_path).schema_version()
    except Exception as exc:  # noqa: BLE001 - diagnostics must never crash
        return f"unreadable ({exc.__class__.__name__})"
    return f"v{version}"


def _remote_state(settings: Settings) -> str:
    if settings.is_loopback:
        return "loopback-only"
    if settings.auth_token:
        return "non-loopback with auth token"
    if settings.allow_insecure_remote:
        return "INSECURE REMOTE (override set, no auth token)"
    return "non-loopback refused (no override)"


def collect(settings: Settings, *, chains_file: Path | str | None = None) -> dict[str, Any]:
    """Build the diagnostics payload without touching the network."""
    chains = load_chains(chains_file)
    all_keys = sorted(chains.chains)
    enabled = settings.chains or all_keys
    dataset_dir = settings.dataset_dir
    return {
        "product": PRODUCT_NAME,
        "version": __version__,
        "license": LICENSE_NAME,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "schema_version": _schema_state(settings.db_path),
        "chains_configured": len(all_keys),
        "chains_enabled": len([key for key in enabled if key in chains.chains]),
        "claim_handoff": "enabled" if settings.claim_handoff else "disabled",
        "read_only": settings.read_only,
        "remote_guard": _remote_state(settings),
        "auth_required": bool(settings.auth_token),
        "rate_limit_enabled": settings.rate_limit_enabled,
        "database": _database_filename(settings.db_path),
        "database_exists": settings.db_path.exists(),
        "dataset_dir_configured": dataset_dir is not None,
        "dataset_dir_present": bool(dataset_dir and dataset_dir.is_dir()),
        "demo_mode": settings.demo,
        "platform": sys.platform,
    }


def render_text(payload: dict[str, Any]) -> str:
    rows = [
        ("product", payload["product"]),
        ("version", payload["version"]),
        ("license", payload["license"]),
        ("python", f"{payload['python']} ({payload['python_implementation']})"),
        ("os", payload["os"]),
        ("schema", payload["schema_version"]),
        ("chains", f"{payload['chains_configured']} configured, {payload['chains_enabled']} enabled"),
        ("claim hand-off", payload["claim_handoff"]),
        ("remote guard", payload["remote_guard"]),
        ("auth token", "required" if payload["auth_required"] else "not set"),
        ("database", payload["database"]),
        (
            "dataset dir",
            "present"
            if payload["dataset_dir_present"]
            else ("configured, missing" if payload["dataset_dir_configured"] else "not configured"),
        ),
        ("demo mode", "on" if payload["demo_mode"] else "off"),
    ]
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}}  {value}" for label, value in rows)
