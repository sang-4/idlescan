"""Configuration from environment / .env.

The one switch that matters: ``IDLESCAN_READ_ONLY``.  It defaults to ``true`` and
the platform is a pure read-only research tool in that state.  Setting it to
``false`` does **not** hand the server any keys - it only unlocks the endpoints
that hand an unsigned transaction to the user's own browser wallet for signing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRUTHY = {"1", "true", "yes", "on", "y"}
FALSEY = {"0", "false", "no", "off", "n"}


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

    @property
    def write_enabled(self) -> bool:
        return not self.read_only

    @property
    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    def validate(self) -> list[str]:
        """Return warnings; raise SettingsError for unsafe combinations."""
        warnings: list[str] = []
        if not self.write_enabled:
            return warnings
        if not self.is_loopback and not self.allow_remote_write:
            raise SettingsError(
                "refusing to start with write mode enabled on a non-loopback host "
                f"({self.host}). Put the server behind TLS/auth and set "
                "IDLESCAN_ALLOW_REMOTE_WRITE=true if you really mean it."
            )
        warnings.append(
            "WRITE MODE: claim endpoints can return unsigned transactions for wallet "
            "signing. The server never holds keys and never broadcasts; review every "
            "transaction in your wallet before approving."
        )
        return warnings

    def with_overrides(self, **kwargs) -> "Settings":
        clean = {key: value for key, value in kwargs.items() if value is not None}
        return replace(self, **clean)


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
        db_path=Path(get("IDLESCAN_DB", "data/idlescan.db") or "data/idlescan.db").expanduser(),
        chains=_as_list(get("IDLESCAN_CHAINS"), []),
        min_usd=_as_float(get("IDLESCAN_MIN_USD"), 1.0),
        refresh_minutes=_as_int(get("IDLESCAN_REFRESH_MINUTES"), 0),
        allow_remote_write=_as_bool(get("IDLESCAN_ALLOW_REMOTE_WRITE"), False),
        dataset_dir=Path(v).expanduser() if (v := get("IDLESCAN_DATASET_DIR")) else None,
        env_file=path,
        demo=_as_bool(get("IDLESCAN_DEMO"), False),
    )
    if not settings.db_path.is_absolute():
        settings.db_path = (PROJECT_ROOT / settings.db_path).resolve()
    return settings
