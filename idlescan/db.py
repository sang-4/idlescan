"""SQLite persistence.

One file, no ORM, no migrations framework - `init()` is idempotent and the schema
version is tracked in `meta` so future changes can be applied in place.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = 1

SCHEMA = """
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
DROP INDEX IF EXISTS idx_alerts_unique;
DELETE FROM alerts WHERE id NOT IN (
    SELECT MIN(id) FROM alerts
    GROUP BY kind, IFNULL(wallet,''), IFNULL(drop_id,''), title
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_unique
    ON alerts(kind, IFNULL(wallet,''), IFNULL(drop_id,''), title);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._initialised = False

    # ------------------------------------------------------------- plumbing
    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init(self) -> "Database":
        with self._lock, self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
        self._initialised = True
        return self

    # -------------------------------------------------------------- wallets
    def add_wallet(self, address: str, label: str = "", tags: str = "") -> dict:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO wallets(address, label, tags, archived, created_at) "
                "VALUES(?, ?, ?, 0, ?) "
                "ON CONFLICT(address) DO UPDATE SET label=excluded.label, tags=excluded.tags",
                (address.lower(), label, tags, now()),
            )
        return self.get_wallet(address)

    def get_wallet(self, address: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM wallets WHERE address = ?", (address.lower(),)
            ).fetchone()
        return dict(row) if row else None

    def list_wallets(self, *, include_archived: bool = False) -> list[dict]:
        query = "SELECT * FROM wallets"
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY created_at"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query)]

    def remove_wallet(self, address: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM wallets WHERE address = ?", (address.lower(),))
        return cursor.rowcount > 0

    # -------------------------------------------------------------- airdrops
    def upsert_airdrop(self, drop_id: str, payload: dict, *, enabled: bool = True, source: str = "registry") -> None:
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO airdrops(id, payload, enabled, source, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
                "enabled=excluded.enabled, updated_at=excluded.updated_at",
                (drop_id, json.dumps(payload), 1 if enabled else 0, source, timestamp, timestamp),
            )

    def list_airdrops(self, *, enabled_only: bool = False) -> list[dict]:
        query = "SELECT * FROM airdrops"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id"
        with self.connect() as connection:
            rows = []
            for row in connection.execute(query):
                item = dict(row)
                item["payload"] = json.loads(item["payload"])
                rows.append(item)
            return rows

    def get_airdrop(self, drop_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM airdrops WHERE id = ?", (drop_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        return item

    def delete_airdrop(self, drop_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM airdrops WHERE id = ? AND source != 'registry'", (drop_id,)
            )
        return cursor.rowcount > 0

    def set_airdrop_enabled(self, drop_id: str, enabled: bool) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE airdrops SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, now(), drop_id),
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ jobs
    def create_job(self, job_id: str, kind: str) -> dict:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO jobs(id, kind, status, message, progress, created_at) "
                "VALUES(?, ?, 'queued', 'queued', 0, ?)",
                (job_id, kind, now()),
            )
        return self.get_job(job_id)

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {"status", "message", "progress", "error", "finished_at", "result"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        if updates.get("result") is not None and not isinstance(updates["result"], str):
            updates["result"] = json.dumps(updates["result"])
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?", (*updates.values(), job_id)
            )

    def get_job(self, job_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        if item.get("result"):
            item["result"] = json.loads(item["result"])
        return item

    def recent_jobs(self, limit: int = 20) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------- snapshots
    def save_snapshot(self, *, chains: Sequence[str], wallets: Sequence[str], totals: dict, report: dict, trigger: str = "manual") -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots(created_at, chains, wallets, trigger, totals, report) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (
                    now(),
                    json.dumps(list(chains)),
                    json.dumps(list(wallets)),
                    trigger,
                    json.dumps(totals),
                    json.dumps(report),
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            for holding in report.get("holdings", []):
                connection.execute(
                    "INSERT INTO holdings(snapshot_id, chain, wallet, asset, asset_address, decimals, raw, usd_price, usd_value) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        snapshot_id,
                        holding["chain"],
                        holding["wallet"],
                        holding["asset"],
                        holding.get("asset_address"),
                        int(holding["decimals"]),
                        str(holding["raw"]),
                        holding.get("usd_price"),
                        holding.get("usd_value"),
                    ),
                )
            for claim in report.get("claims", []):
                connection.execute(
                    "INSERT INTO claims(snapshot_id, drop_id, name, chain, wallet, distributor, status, "
                    "expired, deadline, amount_raw, decimals, token, usd_price, usd_value, gas_native, gas_usd, "
                    "net_usd, proof_verified, eligibility_source, note, calldata) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        snapshot_id,
                        claim["drop_id"],
                        claim["name"],
                        claim["chain"],
                        claim["wallet"],
                        claim["distributor"],
                        claim["status"],
                        1 if claim.get("expired") else 0,
                        claim.get("deadline"),
                        str(claim.get("amount_raw", "0")),
                        int(claim.get("decimals", 18)),
                        claim.get("token"),
                        claim.get("usd_price"),
                        claim.get("usd_value"),
                        claim.get("gas_native"),
                        claim.get("gas_usd"),
                        claim.get("net_usd"),
                        None if claim.get("proof_verified") is None else (1 if claim["proof_verified"] else 0),
                        claim.get("eligibility_source"),
                        claim.get("note"),
                        claim.get("claim_calldata"),
                    ),
                )
        return snapshot_id

    def latest_snapshot(self) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return self._snapshot_row(row)

    def get_snapshot(self, snapshot_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        return self._snapshot_row(row)

    def list_snapshots(self, limit: int = 30) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, chains, wallets, trigger, totals FROM snapshots "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                **{key: row[key] for key in ("id", "created_at", "trigger")},
                "chains": json.loads(row["chains"]),
                "wallets": json.loads(row["wallets"]),
                "totals": json.loads(row["totals"]),
            }
            for row in rows
        ]

    @staticmethod
    def _snapshot_row(row: sqlite3.Row | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "chains": json.loads(row["chains"]),
            "wallets": json.loads(row["wallets"]),
            "trigger": row["trigger"],
            "totals": json.loads(row["totals"]),
            "report": json.loads(row["report"]),
        }

    def holdings(self, snapshot_id: int, *, wallet: str | None = None) -> list[dict]:
        query = "SELECT * FROM holdings WHERE snapshot_id = ?"
        params: list[Any] = [snapshot_id]
        if wallet:
            query += " AND wallet = ?"
            params.append(wallet.lower())
        query += " ORDER BY IFNULL(usd_value, 0) DESC"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def claims(
        self,
        snapshot_id: int,
        *,
        status: str | None = None,
        wallet: str | None = None,
        drop_id: str | None = None,
        min_net: float | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM claims WHERE snapshot_id = ?"
        params: list[Any] = [snapshot_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        if wallet:
            query += " AND wallet = ?"
            params.append(wallet.lower())
        if drop_id:
            query += " AND drop_id = ?"
            params.append(drop_id)
        if min_net is not None:
            query += " AND IFNULL(net_usd, 0) >= ?"
            params.append(min_net)
        query += " ORDER BY CASE status WHEN 'unclaimed' THEN 0 WHEN 'claimed' THEN 1 ELSE 2 END, IFNULL(net_usd, 0) DESC"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def claim(self, snapshot_id: int, drop_id: str, wallet: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM claims WHERE snapshot_id = ? AND drop_id = ? AND wallet = ?",
                (snapshot_id, drop_id, wallet.lower()),
            ).fetchone()
        return dict(row) if row else None

    def previous_snapshot(self, snapshot_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM snapshots WHERE id < ? ORDER BY id DESC LIMIT 1", (snapshot_id,)
            ).fetchone()
        return self._snapshot_row(row)

    def drop_history(self, drop_id: str, limit: int = 40) -> list[dict]:
        """Per-snapshot rollup for one airdrop, oldest first for charting."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT s.id,
                           s.created_at,
                           COUNT(*) AS positions,
                           SUM(CASE WHEN c.status = 'unclaimed' AND c.expired = 0 THEN 1 ELSE 0 END) AS unclaimed,
                           SUM(CASE WHEN c.status = 'claimed' THEN 1 ELSE 0 END) AS claimed,
                           SUM(CASE WHEN c.status = 'unclaimed' AND c.expired = 0
                                    THEN IFNULL(c.usd_value, 0) ELSE 0 END) AS gross_usd,
                           SUM(CASE WHEN c.status = 'unclaimed' AND c.expired = 0
                                    THEN IFNULL(c.net_usd, 0) ELSE 0 END) AS net_usd
                    FROM snapshots s
                    JOIN claims c ON c.snapshot_id = s.id AND c.drop_id = ?
                    GROUP BY s.id
                    ORDER BY s.id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (drop_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def wallet_history(self, address: str, limit: int = 40) -> list[dict]:
        """Per-snapshot idle/unclaimed rollup for one wallet, oldest first."""
        address = address.lower()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT s.id,
                           s.created_at,
                           (SELECT IFNULL(SUM(h.usd_value), 0) FROM holdings h
                             WHERE h.snapshot_id = s.id AND h.wallet = ?) AS idle_usd,
                           (SELECT IFNULL(SUM(c.usd_value), 0) FROM claims c
                             WHERE c.snapshot_id = s.id AND c.wallet = ?
                               AND c.status = 'unclaimed' AND c.expired = 0) AS unclaimed_usd
                    FROM snapshots s
                    ORDER BY s.id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (address, address, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def previous_claims(self, snapshot_id: int) -> dict[tuple[str, str], str]:
        with self.connect() as connection:
            previous = connection.execute(
                "SELECT id FROM snapshots WHERE id < ? ORDER BY id DESC LIMIT 1", (snapshot_id,)
            ).fetchone()
            if not previous:
                return {}
            rows = connection.execute(
                "SELECT drop_id, wallet, status FROM claims WHERE snapshot_id = ?",
                (previous["id"],),
            ).fetchall()
        return {(row["drop_id"], row["wallet"]): row["status"] for row in rows}

    # ---------------------------------------------------------------- alerts
    def add_alert(self, *, kind: str, severity: str, title: str, body: str, wallet: str | None = None, drop_id: str | None = None, snapshot_id: int | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO alerts(created_at, kind, severity, title, body, wallet, drop_id, snapshot_id) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), kind, severity, title, body, wallet, drop_id, snapshot_id),
            )

    def list_alerts(self, *, include_ack: bool = False, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM alerts"
        if not include_ack:
            query += " WHERE acknowledged = 0"
        query += " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id DESC LIMIT ?"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, (limit,))]

    def ack_alert(self, alert_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
        return cursor.rowcount > 0

    def ack_all_alerts(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute("UPDATE alerts SET acknowledged = 1 WHERE acknowledged = 0")
        return cursor.rowcount

    # ------------------------------------------------------------ executions
    def add_execution(self, *, wallet: str, drop_id: str, chain: str, tx_hash: str | None, status: str, detail: str = "") -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO executions(created_at, wallet, drop_id, chain, tx_hash, status, detail) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (now(), wallet.lower(), drop_id, chain, tx_hash, status, detail),
            )
        return int(cursor.lastrowid)

    def list_executions(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM executions ORDER BY id DESC LIMIT ?", (limit,)
            )]

    # ----------------------------------------------------------------- audit
    def log(self, actor: str, action: str, detail: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit(created_at, actor, action, detail) VALUES(?, ?, ?, ?)",
                (now(), actor, action, detail),
            )

    def list_audit(self, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            )]

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict:
        latest = self.latest_snapshot()
        result: dict[str, Any] = {
            "wallets": len(self.list_wallets()),
            "airdrops": len(self.list_airdrops(enabled_only=True)),
            "unacked_alerts": len(self.list_alerts()),
            "snapshot": None,
        }
        if latest:
            totals = latest["totals"]
            claims = self.claims(latest["id"])
            unclaimed = [c for c in claims if c["status"] == "unclaimed" and not c["expired"]]
            result["snapshot"] = {
                "id": latest["id"],
                "created_at": latest["created_at"],
                "idle_usd": totals.get("idle_usd"),
                "unclaimed_usd": totals.get("unclaimed_usd"),
                "combined_usd": totals.get("combined_usd"),
                "unclaimed_count": len(unclaimed),
                "claim_gas_usd": sum(c["gas_usd"] or 0 for c in unclaimed),
                "positions": len(claims),
            }
        return result
