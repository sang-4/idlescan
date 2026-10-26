"""Ordered SQLite migrations.

The v1.1 database was created by an idempotent ``CREATE TABLE IF NOT EXISTS``
script with a version number.  v1.2 replaces that with an append-only list of
numbered migrations so a database can be upgraded in place, audited, and tested.

Rules:

* migrations only ever add columns/tables or rebuild indexes; rows are preserved;
* each migration runs in one transaction together with the version bump;
* a database newer than the code is refused instead of being opened read-write;
* an existing database is copied to ``<db>.pre-v<N>.bak`` before the first pending
  migration, and the migration does not start if that backup fails.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

LATEST_SCHEMA_VERSION = 2

# The exact v1.1 schema, including its alert dedupe behaviour.  Fresh databases
# start here and then run the same migrations a v1.1 install would run.
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS airdrops (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'registry',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    result TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    chains TEXT NOT NULL,
    wallets TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'manual',
    totals TEXT NOT NULL,
    report TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdings (
    snapshot_id INTEGER NOT NULL,
    chain TEXT NOT NULL,
    wallet TEXT NOT NULL,
    asset TEXT NOT NULL,
    asset_address TEXT,
    decimals INTEGER NOT NULL,
    raw TEXT NOT NULL,
    usd_price REAL,
    usd_value REAL
);

CREATE TABLE IF NOT EXISTS claims (
    snapshot_id INTEGER NOT NULL,
    drop_id TEXT NOT NULL,
    name TEXT NOT NULL,
    chain TEXT NOT NULL,
    wallet TEXT NOT NULL,
    distributor TEXT NOT NULL,
    status TEXT NOT NULL,
    expired INTEGER NOT NULL DEFAULT 0,
    deadline TEXT,
    amount_raw TEXT NOT NULL DEFAULT '0',
    decimals INTEGER NOT NULL DEFAULT 18,
    token TEXT,
    usd_price REAL,
    usd_value REAL,
    gas_native REAL,
    gas_usd REAL,
    net_usd REAL,
    proof_verified INTEGER,
    eligibility_source TEXT,
    note TEXT,
    calldata TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    wallet TEXT,
    drop_id TEXT,
    snapshot_id INTEGER,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    wallet TEXT NOT NULL,
    drop_id TEXT NOT NULL,
    chain TEXT NOT NULL,
    tx_hash TEXT,
    status TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_claims_snapshot ON claims(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_claims_wallet ON claims(wallet);
CREATE INDEX IF NOT EXISTS idx_holdings_snapshot ON holdings(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_alerts_ack ON alerts(acknowledged);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_unique
    ON alerts(kind, IFNULL(wallet,''), IFNULL(drop_id,''), title);
"""


class MigrationError(RuntimeError):
    """A migration could not be applied safely."""


class SchemaTooNew(MigrationError):
    """The database was written by a newer version of idlescan."""


class BackupFailed(MigrationError):
    """The pre-migration backup could not be written."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


_CLAIM_GAS_COLUMNS = {
    "gas_units": "INTEGER",
    "fee_per_gas": "INTEGER",
    "max_fee_per_gas": "INTEGER",
    "max_priority_fee_per_gas": "INTEGER",
    "l1_fee_wei": "INTEGER",
    "gas_cost_native_raw": "REAL",
    "gas_cost_usd_raw": "REAL",
    "gas_cost_native_buffered": "REAL",
    "gas_cost_usd_buffered": "REAL",
    "gas_estimate_quality": "TEXT",
    "gas_note": "TEXT",
    "gas_limit": "INTEGER",
    "fee_source": "TEXT",
    "fee_model": "TEXT",
    "root": "TEXT",
    "price_source": "TEXT",
    "price_asof": "TEXT",
    "price_confidence": "TEXT",
}

_NEW_TABLES = """
CREATE TABLE IF NOT EXISTS dataset_provenance (
    drop_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    source_url TEXT,
    fetched_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL DEFAULT 0,
    root_verified INTEGER NOT NULL DEFAULT 0,
    root TEXT,
    block_number INTEGER,
    entry_count INTEGER NOT NULL DEFAULT 0,
    tool_version TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS custom_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decimals INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    UNIQUE(chain, address)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    alert_id INTEGER,
    kind TEXT NOT NULL DEFAULT '',
    url_redacted TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    dead_letter INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);
CREATE INDEX IF NOT EXISTS idx_custom_tokens_chain ON custom_tokens(chain);
"""


def _add_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {
        row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, sql_type in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def _execute_script(connection: sqlite3.Connection, script: str) -> None:
    """Run a script statement-by-statement so it stays in the migration transaction.

    ``sqlite3.executescript`` commits any open transaction implicitly, which would
    break the all-or-nothing guarantee this module provides.
    """
    for statement in script.split(";"):
        if statement.strip():
            connection.execute(statement)


def _migration_1(connection: sqlite3.Connection) -> None:
    _execute_script(connection, SCHEMA_V1)


def _migration_2(connection: sqlite3.Connection) -> None:
    _add_columns(connection, "claims", _CLAIM_GAS_COLUMNS)
    _add_columns(connection, "airdrops", {"provenance": "TEXT"})
    _add_columns(
        connection,
        "alerts",
        {
            "dedupe_key": "TEXT",
            "tier": "TEXT",
            "resolved_at": "TEXT",
            "resolved_reason": "TEXT",
            "notified_at": "TEXT",
        },
    )
    _add_columns(
        connection,
        "jobs",
        {
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "phases": "TEXT",
            "timeout_at": "TEXT",
            "request_id": "TEXT",
        },
    )
    _add_columns(connection, "snapshots", {"duration_ms": "INTEGER", "phases": "TEXT"})
    connection.execute("DROP INDEX IF EXISTS idx_alerts_unique")
    connection.execute(
        "UPDATE alerts SET dedupe_key = kind || '|' || IFNULL(wallet, '') || '|' || "
        "IFNULL(drop_id, '') || '|' || title WHERE dedupe_key IS NULL"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_dedupe_open "
        "ON alerts(dedupe_key) WHERE dedupe_key IS NOT NULL AND resolved_at IS NULL"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_alerts_dedupe ON alerts(dedupe_key)"
    )
    _execute_script(connection, _NEW_TABLES)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial v1.1 schema", _migration_1),
    Migration(2, "gas model, provenance, alert scopes, custom tokens", _migration_2),
)

MIGRATIONS_BY_VERSION = {migration.version: migration for migration in MIGRATIONS}


def schema_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        raise MigrationError("meta.schema_version is not an integer") from None


def _backup(connection: sqlite3.Connection, path: Path, version: int) -> Path:
    target = path.with_name(f"{path.name}.pre-v{version}.bak")
    try:
        if target.exists():
            target.unlink()
        with sqlite3.connect(target) as destination:
            connection.backup(destination)
        if not target.is_file() or target.stat().st_size == 0:
            raise OSError("backup file is empty")
    except (OSError, sqlite3.Error) as exc:
        raise BackupFailed(
            f"could not write pre-migration backup {target.name} ({exc}); "
            "refusing to migrate. Free disk space or copy the database manually."
        ) from exc
    return target


def apply_migrations(
    connection: sqlite3.Connection,
    path: Path,
    *,
    backup: bool = True,
    on_backup: Callable[[Path], None] | None = None,
) -> list[int]:
    """Apply all pending migrations. Returns the versions applied."""
    current = schema_version(connection)
    if current > LATEST_SCHEMA_VERSION:
        raise SchemaTooNew(
            f"database schema v{current} is newer than this version of idlescan "
            f"(supports v{LATEST_SCHEMA_VERSION}). Upgrade idlescan or restore a backup."
        )
    pending = [
        migration for migration in MIGRATIONS if migration.version > current
    ]
    if not pending:
        return []
    if backup and current > 0 and path.exists():
        target = _backup(connection, path, current)
        if on_backup is not None:
            on_backup(target)
    applied: list[int] = []
    for migration in pending:
        try:
            connection.execute("BEGIN IMMEDIATE")
            migration.apply(connection)
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(migration.version),),
            )
            connection.execute("COMMIT")
        except Exception as exc:
            connection.execute("ROLLBACK")
            raise MigrationError(
                f"migration {migration.version} ({migration.name}) failed: {exc}. "
                "The database was rolled back; the pre-migration backup is still available."
            ) from exc
        applied.append(migration.version)
    return applied


def restore_backup(path: Path, version: int, *, destination: Path | None = None) -> Path:
    """Copy a pre-migration backup back into place (operator tool)."""
    source = path.with_name(f"{path.name}.pre-v{version}.bak")
    if not source.is_file():
        raise FileNotFoundError(source)
    target = destination or path
    shutil.copy2(source, target)
    return target
