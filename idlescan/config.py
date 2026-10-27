"""Configuration from environment / ``.env``.

``IDLESCAN_READ_ONLY`` is still the canonical switch.  In plain language:

* **claim hand-off disabled** (read-only, the default) - the server will not
  return an unsigned claim transaction;
* **claim hand-off enabled** (``IDLESCAN_READ_ONLY=false``) - the server may
  return an unsigned transaction for the user's own wallet to sign;
* **local edits allowed/locked** (``IDLESCAN_LOCKDOWN``) - a separate switch
  that blocks administrative writes while leaving reads, exports and the
  ephemeral address checker available.

The server never holds keys, never signs and never broadcasts in any mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRUTHY = {"1", "true", "yes", "on", "y"}
FALSEY = {"0", "false", "no", "off", "n"}

DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_MAX_FETCH_BYTES = 25 * 1024 * 1024
LOG_FORMATS = ("text", "json")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
SEVERITIES = ("info", "warning", "critical")


class SettingsError(ValueError):
    pass


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal ``.env`` parser: KEY=VALUE, ``#`` comments, optional quotes."""
    values: dict[str, str] = {}
    try:
        text = path.read_text()
    except FileNotFoundError:
        return values
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise SettingsError(f"{path}:{number}: expected KEY=VALUE, got {raw_line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _as_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = value.strip().lower()
    if lowered in TRUTHY:
        return True
    if lowered in FALSEY:
        return False
    raise SettingsError(f"cannot read {value!r} as a boolean")


def _as_int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise SettingsError(f"cannot read {value!r} as an integer") from None


def _as_float(value: str | None, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        raise SettingsError(f"cannot read {value!r} as a number") from None


def _as_list(value: str | None, default: list[str]) -> list[str]:
    if value is None or not value.strip():
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def find_env_file(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise SettingsError(f".env file not found: {path}")
        return path
    for candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        if candidate.exists():
            return candidate
    return None


@dataclass
class Settings:
    read_only: bool = True
    host: str = "127.0.0.1"
    port: int = 8787
    db_path: Path = field(default_factory=lambda: Path.cwd() / "data" / "idlescan.db")
    chains: list[str] = field(default_factory=list)
    min_usd: float = 1.0
    refresh_minutes: int = 0
    allow_remote_write: bool = False
    dataset_dir: Path | None = None
    env_file: Path | None = None
    demo: bool = False

    # WP-01 / WP-13
    gas_buffer_pct: int = 20
    scan_workers: int = 4
    scan_timeout_seconds: int = 300
    price_disagreement_pct: float = 10.0

    # WP-04
    auth_token: str | None = None
    allow_insecure_remote: bool = False
    lockdown: bool = False
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    rate_limit_loopback: bool = False
    rate_limit_read_per_minute: int = 240
    rate_limit_write_per_minute: int = 60
    rate_limit_scan_per_ten_minutes: int = 12

    # WP-03
    allow_http_fetch: bool = False
    max_fetch_bytes: int = DEFAULT_MAX_FETCH_BYTES
    fetch_allowed_hosts: list[str] = field(default_factory=list)
    fetch_timeout_seconds: float = 15.0

    # WP-05
    rpc_quorum: int = 1
    rpc_overrides: dict[str, list[str]] = field(default_factory=dict)

    # WP-07
    dataset_stale_days: int = 30

    # WP-08
    log_format: str = "text"
    log_level: str = "INFO"
    http_log: bool = False

    # WP-09
    notify_webhook_urls: list[str] = field(default_factory=list)
    notify_min_severity: str = "warning"
    notify_kinds: list[str] = field(default_factory=list)
    notify_digest: bool = False

    # WP-06
    backup_keep: int = 5
    retention_snapshots: int = 90
    retention_alerts_days: int = 180
    retention_audit_days: int = 365
    retention_executions_days: int = 365
    retention_jobs_days: int = 30

    @property
    def write_enabled(self) -> bool:
        return not self.read_only

    @property
    def claim_handoff(self) -> bool:
        """Plain-language name for write mode (additive API field)."""
        return self.write_enabled

    @property
    def local_edits(self) -> bool:
        """Whether administrative writes are currently allowed."""
        return not self.lockdown

    @property
    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    @property
    def rate_limit_enabled(self) -> bool:
        return (not self.is_loopback) or self.rate_limit_loopback

    def validate(self) -> list[str]:
        """Return warnings; raise SettingsError for unsafe combinations."""
        warnings: list[str] = []
        errors = self._range_errors()
        if errors:
            raise SettingsError("; ".join(errors))

        if not self.is_loopback:
            if not self.auth_token and not self.allow_insecure_remote:
                raise SettingsError(
                    f"refusing to start on non-loopback host {self.host} without "
                    "IDLESCAN_AUTH_TOKEN. Set a token (recommended) or explicitly accept "
                    "the risk with IDLESCAN_ALLOW_INSECURE_REMOTE=true."
                )
            if self.allow_insecure_remote and not self.auth_token:
                warnings.append(
                    "INSECURE REMOTE: no authentication token is set. Anyone who can "
                    "reach this host can read the database and use the API."
                )
            if self.write_enabled and not self.allow_remote_write:
                raise SettingsError(
                    "refusing to start with claim hand-off enabled on a non-loopback "
                    f"host ({self.host}). Set IDLESCAN_ALLOW_REMOTE_WRITE=true only "
                    "behind TLS and authentication."
                )
        if self.write_enabled:
            warnings.append(
                "CLAIM HAND-OFF ENABLED: the server can return unsigned transactions "
                "for wallet signing. It never holds keys and never broadcasts; review "
                "every transaction in your wallet before approving."
            )
        if self.lockdown:
            warnings.append(
                "LOCKDOWN: local edits (wallet/airdrop changes, alerts, backups, scans) "
                "are disabled. Reads, exports and POST /api/check still work."
            )
        if self.notify_webhook_urls:
            warnings.append(
                "notifications enabled: alert summaries will be POSTed to "
                f"{len(self.notify_webhook_urls)} webhook URL(s)"
            )
        return warnings

    def _range_errors(self) -> list[str]:
        errors: list[str] = []
        if not 0 <= self.port <= 65535:
            errors.append(f"IDLESCAN_PORT must be 0-65535, got {self.port}")
        if not 0 <= self.gas_buffer_pct <= 100:
            errors.append(
                f"IDLESCAN_GAS_BUFFER_PCT must be 0-100, got {self.gas_buffer_pct}"
            )
        if not 1 <= self.scan_workers <= 32:
            errors.append(f"IDLESCAN_SCAN_WORKERS must be 1-32, got {self.scan_workers}")
        if self.scan_timeout_seconds <= 0:
            errors.append("IDLESCAN_SCAN_TIMEOUT_SECONDS must be positive")
        if not 1 <= self.rpc_quorum <= 10:
            errors.append(f"IDLESCAN_RPC_QUORUM must be 1-10, got {self.rpc_quorum}")
        if self.max_body_bytes < 1024:
            errors.append("IDLESCAN_MAX_BODY_BYTES must be at least 1024")
        if self.max_fetch_bytes < 1024:
            errors.append("IDLESCAN_MAX_FETCH_BYTES must be at least 1024")
        if self.dataset_stale_days <= 0:
            errors.append("IDLESCAN_DATASET_STALE_DAYS must be positive")
        if self.log_format not in LOG_FORMATS:
            errors.append(
                f"IDLESCAN_LOG_FORMAT must be one of {', '.join(LOG_FORMATS)}"
            )
        if self.log_level.upper() not in LOG_LEVELS:
            errors.append(f"IDLESCAN_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")
        if self.notify_min_severity not in SEVERITIES:
            errors.append(
                "IDLESCAN_NOTIFY_MIN_SEVERITY must be one of " + ", ".join(SEVERITIES)
            )
        if self.backup_keep < 1:
            errors.append("IDLESCAN_BACKUP_KEEP must be at least 1")
        for name, value in (
            ("retention_snapshots", self.retention_snapshots),
            ("retention_alerts_days", self.retention_alerts_days),
            ("retention_audit_days", self.retention_audit_days),
            ("retention_executions_days", self.retention_executions_days),
            ("retention_jobs_days", self.retention_jobs_days),
        ):
            if value < 0:
                errors.append(f"IDLESCAN_{name.upper()} must be zero or positive (0 keeps all)")
        return errors

    def rpc_endpoints(self, chain) -> list[str]:
        """Operator overrides take precedence over bundled endpoints."""
        override = self.rpc_overrides.get(getattr(chain, "key", ""))
        return list(override or getattr(chain, "rpcs", ()))

    def with_overrides(self, **kwargs) -> "Settings":
        clean = {key: value for key, value in kwargs.items() if value is not None}
        return replace(self, **clean)


def _rpc_overrides(
    environ: dict[str, str], file_values: dict[str, str]
) -> dict[str, list[str]]:
    """Collect ``IDLESCAN_RPC_<CHAINKEY>`` overrides, taking precedence."""
    overrides: dict[str, list[str]] = {}
    for source in (file_values, environ):
        for key, value in source.items():
            if not key.startswith("IDLESCAN_RPC_") or key == "IDLESCAN_RPC_QUORUM":
                continue
            chain_key = key[len("IDLESCAN_RPC_") :].lower()
            urls = [item.strip() for item in value.split(",") if item.strip()]
            if urls:
                overrides[chain_key] = urls
    return overrides


def _default_db_path(value: str | None) -> Path:
    """Resolve the database path without ever defaulting inside site-packages.

    An explicit ``IDLESCAN_DB`` keeps its historical behaviour: relative paths
    resolve against the project root.  With no setting at all the database lives
    under the current working directory, so a wheel installed into a system or
    virtual environment can still write its data.
    """
    if value:
        return Path(value).expanduser()
    return (Path.cwd() / "data" / "idlescan.db").expanduser()


def load_settings(env_file: str | Path | None = None, *, environ: dict[str, str] | None = None) -> Settings:
    environ = dict(os.environ if environ is None else environ)
    path = find_env_file(env_file)
    file_values = parse_env_file(path) if path else {}

    def get(key: str, default: str | None = None) -> str | None:
        return environ.get(key, file_values.get(key, default))

    settings = Settings(
        read_only=_as_bool(get("IDLESCAN_READ_ONLY"), True),
        host=get("IDLESCAN_HOST", "127.0.0.1") or "127.0.0.1",
        port=_as_int(get("IDLESCAN_PORT"), 8787),
        db_path=_default_db_path(get("IDLESCAN_DB")),
        chains=_as_list(get("IDLESCAN_CHAINS"), []),
        min_usd=_as_float(get("IDLESCAN_MIN_USD"), 1.0),
        refresh_minutes=_as_int(get("IDLESCAN_REFRESH_MINUTES"), 0),
        allow_remote_write=_as_bool(get("IDLESCAN_ALLOW_REMOTE_WRITE"), False),
        dataset_dir=Path(v).expanduser() if (v := get("IDLESCAN_DATASET_DIR")) else None,
        env_file=path,
        demo=_as_bool(get("IDLESCAN_DEMO"), False),
        gas_buffer_pct=_as_int(get("IDLESCAN_GAS_BUFFER_PCT"), 20),
        scan_workers=_as_int(get("IDLESCAN_SCAN_WORKERS"), 4),
        scan_timeout_seconds=_as_int(get("IDLESCAN_SCAN_TIMEOUT_SECONDS"), 300),
        price_disagreement_pct=_as_float(get("IDLESCAN_PRICE_DISAGREEMENT_PCT"), 10.0),
        auth_token=(get("IDLESCAN_AUTH_TOKEN") or "").strip() or None,
        allow_insecure_remote=_as_bool(get("IDLESCAN_ALLOW_INSECURE_REMOTE"), False),
        lockdown=_as_bool(get("IDLESCAN_LOCKDOWN"), False),
        max_body_bytes=_as_int(get("IDLESCAN_MAX_BODY_BYTES"), DEFAULT_MAX_BODY_BYTES),
        rate_limit_loopback=_as_bool(get("IDLESCAN_RATE_LIMIT_LOOPBACK"), False),
        rate_limit_read_per_minute=_as_int(get("IDLESCAN_RATE_LIMIT_READ_PER_MINUTE"), 240),
        rate_limit_write_per_minute=_as_int(get("IDLESCAN_RATE_LIMIT_WRITE_PER_MINUTE"), 60),
        rate_limit_scan_per_ten_minutes=_as_int(
            get("IDLESCAN_RATE_LIMIT_SCAN_PER_10_MINUTES"), 12
        ),
        allow_http_fetch=_as_bool(get("IDLESCAN_ALLOW_HTTP_FETCH"), False),
        max_fetch_bytes=_as_int(get("IDLESCAN_MAX_FETCH_BYTES"), DEFAULT_MAX_FETCH_BYTES),
        fetch_allowed_hosts=_as_list(get("IDLESCAN_FETCH_ALLOWED_HOSTS"), []),
        fetch_timeout_seconds=_as_float(get("IDLESCAN_FETCH_TIMEOUT_SECONDS"), 15.0),
        rpc_quorum=_as_int(get("IDLESCAN_RPC_QUORUM"), 1),
        rpc_overrides=_rpc_overrides(environ, file_values),
        dataset_stale_days=_as_int(get("IDLESCAN_DATASET_STALE_DAYS"), 30),
        log_format=(get("IDLESCAN_LOG_FORMAT", "text") or "text").lower(),
        log_level=(get("IDLESCAN_LOG_LEVEL", "INFO") or "INFO").upper(),
        http_log=_as_bool(get("IDLESCAN_HTTP_LOG"), False),
        notify_webhook_urls=_as_list(get("IDLESCAN_NOTIFY_WEBHOOK_URLS"), []),
        notify_min_severity=(get("IDLESCAN_NOTIFY_MIN_SEVERITY", "warning") or "warning").lower(),
        notify_kinds=_as_list(get("IDLESCAN_NOTIFY_KINDS"), []),
        notify_digest=_as_bool(get("IDLESCAN_NOTIFY_DIGEST"), False),
        backup_keep=_as_int(get("IDLESCAN_BACKUP_KEEP"), 5),
        retention_snapshots=_as_int(get("IDLESCAN_RETENTION_SNAPSHOTS"), 90),
        retention_alerts_days=_as_int(get("IDLESCAN_RETENTION_ALERTS_DAYS"), 180),
        retention_audit_days=_as_int(get("IDLESCAN_RETENTION_AUDIT_DAYS"), 365),
        retention_executions_days=_as_int(get("IDLESCAN_RETENTION_EXECUTIONS_DAYS"), 365),
        retention_jobs_days=_as_int(get("IDLESCAN_RETENTION_JOBS_DAYS"), 30),
    )
    if not settings.db_path.is_absolute():
        settings.db_path = (PROJECT_ROOT / settings.db_path).resolve()
    return settings
