"""``idlescan doctor``: check a deployment before trusting it.

Offline by default.  It reads configuration, parses the registry and datasets,
inspects the database without migrating it, and reports exactly what a new
operator would otherwise discover during a failed scan.  ``--online`` adds one
``eth_blockNumber`` probe per configured chain; nothing else touches the network.
"""

from __future__ import annotations

import importlib
import platform
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .chains import ConfigError, load_chains, load_tokens
from .config import Settings, SettingsError, load_settings
from .db import Database
from .migrations import LATEST_SCHEMA_VERSION
from .registry import load_registry

OK = "ok"
WARN = "warn"
FAIL = "fail"

CORE_MODULES = (
    "idlescan.abi",
    "idlescan.api",
    "idlescan.chains",
    "idlescan.dataset",
    "idlescan.db",
    "idlescan.merkle",
    "idlescan.preflight",
    "idlescan.rpc",
    "idlescan.safe_http",
    "idlescan.scanner",
    "idlescan.service",
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
        }


def _check_writable(path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".doctor-", delete=True):
            pass
    except OSError as exc:
        return Check(
            "filesystem",
            FAIL,
            f"{path} is not writable ({exc.__class__.__name__})",
            "Point IDLESCAN_DB at a writable directory owned by this user.",
        )
    return Check("filesystem", OK, f"{path} is writable")


def _config_checks(settings: Settings) -> list[Check]:
    try:
        warnings = settings.validate()
    except SettingsError as exc:
        return [Check("configuration", FAIL, str(exc), "Fix the flagged environment values.")]
    checks = [
        Check(
            "configuration",
            WARN if warnings else OK,
            "; ".join(warnings) if warnings else "no unsafe combinations",
            "See README configuration for the safe defaults.",
        )
    ]
    if settings.auth_token:
        checks.append(Check("auth", OK, "bearer token configured for /api/*"))
    elif settings.is_loopback:
        checks.append(Check("auth", OK, "loopback-only bind; no token required"))
    else:
        checks.append(
            Check(
                "auth",
                WARN,
                "non-loopback bind without a token",
                "Set IDLESCAN_AUTH_TOKEN before exposing the server.",
            )
        )
    if settings.allow_http_fetch:
        checks.append(
            Check(
                "fetch-policy",
                WARN,
                "plain HTTP and private addresses are allowed for outbound fetches",
                "Unset IDLESCAN_ALLOW_HTTP_FETCH outside local fixture testing.",
            )
        )
    return checks


def _chain_checks(
    settings: Settings,
    chains_file: Path | str | None = None,
    tokens_file: Path | str | None = None,
) -> tuple[list[Check], Any]:
    try:
        chains = load_chains(chains_file)
    except (ConfigError, OSError, ValueError) as exc:
        return [Check("chains", FAIL, f"chain configuration is invalid: {exc}")], None
    count = len(chains.chains)
    empty = [chain.key for chain in chains.chains.values() if not chain.rpcs]
    if empty:
        return [
            Check(
                "chains",
                FAIL,
                f"{count} chains configured but {', '.join(empty)} have no RPC endpoints",
            )
        ], chains
    checks = [Check("chains", OK, f"{count} chains configured")]
    try:
        tokens = load_tokens(chains, tokens_file)
    except (ConfigError, OSError, ValueError) as exc:
        checks.append(Check("tokens", FAIL, f"token list is invalid: {exc}"))
    else:
        total = sum(len(items) for items in tokens.values())
        checks.append(Check("tokens", OK, f"{total} tokens across {len(tokens)} chains"))
    if settings.chains:
        unknown = [key for key in settings.chains if key not in chains.chains]
        checks.append(
            Check(
                "selected-chains",
                FAIL if unknown else OK,
                f"unknown chains: {', '.join(unknown)}" if unknown else "configured chains exist",
            )
        )
    return checks, chains


def _registry_checks(settings: Settings, chains: Any) -> list[Check]:
    try:
        drops = load_registry(None, chains)
    except (ConfigError, OSError, ValueError) as exc:
        return [Check("registry", FAIL, f"registry is invalid: {exc}")]
    enabled = [drop for drop in drops if drop.enabled]
    checks = [Check("registry", OK, f"{len(enabled)} enabled airdrops")]
    missing = []
    stale = []
    now = datetime.now(timezone.utc)
    for drop in enabled:
        eligibility = drop.eligibility
        if eligibility and eligibility.dataset:
            path = Path(eligibility.dataset)
            if not path.is_file():
                missing.append(path.name)
            elif settings.dataset_stale_days > 0:
                age = (now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).days
                if age > settings.dataset_stale_days:
                    stale.append(f"{path.name} ({age}d)")
    if missing:
        checks.append(
            Check(
                "datasets",
                WARN,
                f"missing dataset file(s): {', '.join(sorted(set(missing)))}",
                "Add the published distribution file or disable the drop.",
            )
        )
    if stale:
        checks.append(
            Check(
                "datasets",
                WARN,
                f"stale dataset file(s): {', '.join(sorted(set(stale)))}",
                f"Re-check the source; the threshold is IDLESCAN_DATASET_STALE_DAYS="
                f"{settings.dataset_stale_days}.",
            )
        )
    if not missing and not stale:
        checks.append(Check("datasets", OK, "every referenced dataset is present and fresh"))
    return checks


def _database_checks(settings: Settings) -> list[Check]:
    db = Database(settings.db_path)
    if not settings.db_path.is_file():
        return [
            Check(
                "database",
                WARN,
                f"no database yet at {settings.db_path} (created on first run)",
            )
        ]
    status = db.status()
    checks: list[Check] = []
    if status["schema_version"] > LATEST_SCHEMA_VERSION:
        checks.append(
            Check(
                "database",
                FAIL,
                f"schema v{status['schema_version']} is newer than this release",
                "Upgrade idlescan or restore a backup.",
            )
        )
        return checks
    if status["schema_version"] < LATEST_SCHEMA_VERSION:
        checks.append(
            Check(
                "database",
                WARN,
                f"schema v{status['schema_version']} needs migration to v{LATEST_SCHEMA_VERSION}",
                "Run `idlescan db migrate` (a backup is written automatically).",
            )
        )
    else:
        checks.append(Check("database", OK, f"schema v{status['schema_version']}"))
    result = db.check()
    # A pending migration is already reported above as a warning; only genuine
    # corruption or inconsistency should fail the doctor.
    structural = ("integrity", "foreign_keys", "orphans", "snapshot_json")
    broken = [name for name in structural if not result["checks"].get(name, True)]
    checks.append(
        Check(
            "database-integrity",
            FAIL if broken else OK,
            f"failed: {', '.join(broken)}; "
            + "; ".join(result["problems"][:5])
            if broken
            else "integrity and consistency checks passed",
            "Run `idlescan db check --json` for the full list.",
        )
    )
    return checks


def _import_checks() -> list[Check]:
    failures = []
    for module in CORE_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{module}: {exc.__class__.__name__}")
    if failures:
        return [Check("imports", FAIL, "; ".join(failures), "Reinstall idlescan.")]
    return [Check("imports", OK, f"{len(CORE_MODULES)} core modules import")]


def _online_checks(settings: Settings, chains: Any) -> list[Check]:
    from .rpc import Rpc, RpcError

    checks: list[Check] = []
    selected = chains.resolve(settings.chains or None)
    for chain in selected:
        urls = settings.rpc_endpoints(chain)
        try:
            block = Rpc(urls, timeout=6, retries=1, label=chain.key).block_number()
        except (RpcError, ValueError) as exc:
            checks.append(
                Check(
                    f"rpc:{chain.key}",
                    FAIL,
                    f"no endpoint answered ({exc.__class__.__name__})",
                    "Check IDLESCAN_RPC_<CHAIN> or your network egress.",
                )
            )
        else:
            checks.append(
                Check(f"rpc:{chain.key}", OK, f"block {block} across {len(urls)} endpoint(s)")
            )
    return checks


def run_doctor(
    settings: Settings | None = None,
    *,
    online: bool = False,
    chains_file: Path | str | None = None,
    tokens_file: Path | str | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    checks: list[Check] = []
    checks.append(
        Check(
            "runtime",
            OK if sys.version_info >= (3, 10) else FAIL,
            f"Python {platform.python_version()} on {platform.system()}",
            None if sys.version_info >= (3, 10) else "idlescan requires Python 3.10+.",
        )
    )
    checks.append(Check("version", OK, f"idlescan {__version__}"))
    checks.extend(_import_checks())
    checks.extend(_config_checks(settings))
    chain_checks, chains = _chain_checks(settings, chains_file, tokens_file)
    checks.extend(chain_checks)
    if chains is not None:
        checks.extend(_registry_checks(settings, chains))
    checks.extend(_database_checks(settings))
    checks.append(_check_writable(settings.db_path.parent))
    if settings.dataset_dir:
        checks.append(_check_writable(Path(settings.dataset_dir)))
    if online and chains is not None:
        checks.extend(_online_checks(settings, chains))
    else:
        checks.append(
            Check(
                "network",
                OK,
                "offline mode: no RPC or HTTP request was made",
                "Run `idlescan doctor --online` to probe configured RPC endpoints.",
            )
        )
    failures = sum(1 for check in checks if check.status == FAIL)
    warnings = sum(1 for check in checks if check.status == WARN)
    return {
        "ok": failures == 0,
        "failures": failures,
        "warnings": warnings,
        "online": online,
        "version": __version__,
        "checks": [check.as_dict() for check in checks],
    }
