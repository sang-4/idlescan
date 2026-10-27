"""SQLite persistence.

One file, no ORM. Schema changes are applied by the ordered migrations in
:mod:`idlescan.migrations`; opening an existing database creates a backup before
the first upgrade and refuses files written by a newer release.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .migrations import LATEST_SCHEMA_VERSION, apply_migrations, schema_version
from .redaction import redact_text

SCHEMA_VERSION = LATEST_SCHEMA_VERSION


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
        """Open the database and apply any pending migrations."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with sqlite3.connect(self.path, timeout=15) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=5000")
                applied = apply_migrations(connection, self.path)
                connection.commit()
            self.applied_migrations = applied
        self._initialised = True
        return self

    def schema_version(self) -> int:
        with self.connect() as connection:
            return schema_version(connection)

    def vacuum(self) -> None:
        with self._lock:
            with sqlite3.connect(self.path) as connection:
                connection.execute("VACUUM")

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

    def purge_demo_records(self, demo_addresses: Sequence[str]) -> dict[str, int]:
        """Remove data created by demo mode.

        Demo mode seeds three wallets, a ``demo`` airdrop and snapshots of the
        synthetic portfolio.  Those rows used to survive turning ``IDLESCAN_DEMO``
        off, so the UI kept showing a fake portfolio.  This removes only records
        that can be attributed to demo mode:

        * the three known demo wallet addresses;
        * airdrops whose ``source`` is ``demo``;
        * snapshots triggered by demo mode, or whose wallet set is entirely demo
          addresses (these are scans of the synthetic wallets).

        A real wallet or a user-added airdrop is never touched.
        """
        addresses = {str(address).lower() for address in demo_addresses}
        counts = {
            "wallets": 0,
            "airdrops": 0,
            "snapshots": 0,
            "alerts": 0,
            "notifications": 0,
            "executions": 0,
        }
        with self.connect() as connection:
            if addresses:
                placeholders = ",".join("?" for _ in addresses)
                cursor = connection.execute(
                    f"DELETE FROM wallets WHERE lower(address) IN ({placeholders})",
                    tuple(addresses),
                )
                counts["wallets"] = max(cursor.rowcount, 0)

            demo_drop_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM airdrops WHERE source = 'demo'"
                ).fetchall()
            ]

            snapshot_ids: list[int] = []
            for row in connection.execute("SELECT id, wallets, trigger FROM snapshots").fetchall():
                try:
                    wallets = json.loads(row["wallets"])
                except (TypeError, ValueError):
                    wallets = []
                wallets = [str(wallet).lower() for wallet in wallets]
                only_demo = bool(wallets) and all(wallet in addresses for wallet in wallets)
                if row["trigger"] == "demo" or only_demo:
                    snapshot_ids.append(int(row["id"]))

            alert_ids: set[int] = set()
            if snapshot_ids:
                placeholders = ",".join("?" for _ in snapshot_ids)
                alert_ids.update(
                    row[0]
                    for row in connection.execute(
                        f"SELECT id FROM alerts WHERE snapshot_id IN ({placeholders})",
                        tuple(snapshot_ids),
                    ).fetchall()
                )
            if demo_drop_ids:
                placeholders = ",".join("?" for _ in demo_drop_ids)
                alert_ids.update(
                    row[0]
                    for row in connection.execute(
                        f"SELECT id FROM alerts WHERE drop_id IN ({placeholders})",
                        tuple(demo_drop_ids),
                    ).fetchall()
                )
            if alert_ids:
                placeholders = ",".join("?" for _ in alert_ids)
                counts["notifications"] = max(
                    connection.execute(
                        f"DELETE FROM notifications WHERE alert_id IN ({placeholders})",
                        tuple(alert_ids),
                    ).rowcount,
                    0,
                )
                counts["alerts"] = max(
                    connection.execute(
                        f"DELETE FROM alerts WHERE id IN ({placeholders})",
                        tuple(alert_ids),
                    ).rowcount,
                    0,
                )

            if snapshot_ids:
                placeholders = ",".join("?" for _ in snapshot_ids)
                connection.execute(
                    f"DELETE FROM holdings WHERE snapshot_id IN ({placeholders})",
                    tuple(snapshot_ids),
                )
                connection.execute(
                    f"DELETE FROM claims WHERE snapshot_id IN ({placeholders})",
                    tuple(snapshot_ids),
                )
                connection.execute(
                    f"DELETE FROM alerts WHERE snapshot_id IN ({placeholders})",
                    tuple(snapshot_ids),
                )
                counts["snapshots"] = max(
                    connection.execute(
                        f"DELETE FROM snapshots WHERE id IN ({placeholders})",
                        tuple(snapshot_ids),
                    ).rowcount,
                    0,
                )

            if demo_drop_ids:
                placeholders = ",".join("?" for _ in demo_drop_ids)
                connection.execute(
                    f"DELETE FROM claims WHERE drop_id IN ({placeholders})",
                    tuple(demo_drop_ids),
                )
                connection.execute(
                    f"DELETE FROM dataset_provenance WHERE drop_id IN ({placeholders})",
                    tuple(demo_drop_ids),
                )
                connection.execute(
                    f"DELETE FROM executions WHERE drop_id IN ({placeholders})",
                    tuple(demo_drop_ids),
                )
            if addresses:
                placeholders = ",".join("?" for _ in addresses)
                counts["executions"] += max(
                    connection.execute(
                        f"DELETE FROM executions WHERE lower(wallet) IN ({placeholders})",
                        tuple(addresses),
                    ).rowcount,
                    0,
                )
                connection.execute(
                    f"DELETE FROM alerts WHERE lower(wallet) IN ({placeholders})",
                    tuple(addresses),
                )
            counts["airdrops"] = max(
                connection.execute("DELETE FROM airdrops WHERE source = 'demo'").rowcount,
                0,
            )
        return counts

    def set_airdrop_enabled(self, drop_id: str, enabled: bool) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE airdrops SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, now(), drop_id),
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------- custom tokens
    def add_custom_token(
        self,
        chain: str,
        address: str,
        symbol: str,
        decimals: int,
        *,
        source: str = "manual",
    ) -> dict | None:
        """Add a custom token. Returns None if the (chain, address) already exists."""
        from .abi import normalise_address

        address = normalise_address(address)
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO custom_tokens(chain, address, symbol, decimals, added_at, source) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(chain, address) DO NOTHING",
                (chain.lower(), address, symbol, int(decimals), now(), source),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_custom_token(chain, address)

    def remove_custom_token(self, chain: str, address: str) -> bool:
        from .abi import normalise_address

        address = normalise_address(address)
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM custom_tokens WHERE chain = ? AND address = ?",
                (chain.lower(), address),
            )
        return cursor.rowcount > 0

    def get_custom_token(self, chain: str, address: str) -> dict | None:
        from .abi import normalise_address

        address = normalise_address(address)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM custom_tokens WHERE chain = ? AND address = ?",
                (chain.lower(), address),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def list_custom_tokens(self, chain: str | None = None) -> list[dict]:
        with self.connect() as connection:
            if chain:
                rows = connection.execute(
                    "SELECT * FROM custom_tokens WHERE chain = ? ORDER BY symbol",
                    (chain.lower(),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM custom_tokens ORDER BY chain, symbol"
                ).fetchall()
        return [dict(row) for row in rows]

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
    def save_snapshot(
        self,
        *,
        chains: Sequence[str],
        wallets: Sequence[str],
        totals: dict,
        report: dict,
        trigger: str = "manual",
        duration_ms: int | None = None,
        phases: dict | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots(created_at, chains, wallets, trigger, totals, report, duration_ms, phases) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now(),
                    json.dumps(list(chains)),
                    json.dumps(list(wallets)),
                    trigger,
                    json.dumps(totals),
                    json.dumps(report),
                    duration_ms,
                    json.dumps(phases) if phases is not None else None,
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
                    "net_usd, proof_verified, eligibility_source, note, calldata, "
                    "gas_units, fee_per_gas, max_fee_per_gas, max_priority_fee_per_gas, l1_fee_wei, gas_cost_native_raw, gas_cost_usd_raw, "
                    "gas_cost_native_buffered, gas_cost_usd_buffered, "
                    "gas_estimate_quality, gas_note, gas_limit, fee_source, fee_model, root, "
                    "price_source, price_asof, price_confidence) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        claim.get("gas_units"),
                        claim.get("fee_per_gas"),
                        claim.get("max_fee_per_gas"),
                        claim.get("max_priority_fee_per_gas"),
                        claim.get("l1_fee_wei"),
                        claim.get("gas_cost_native_raw", claim.get("gas_native")),
                        claim.get("gas_cost_usd_raw", claim.get("gas_usd")),
                        claim.get("gas_cost_native_buffered"),
                        claim.get("gas_cost_usd_buffered"),
                        claim.get("gas_estimate_quality"),
                        claim.get("gas_note"),
                        claim.get("gas_limit"),
                        claim.get("fee_source"),
                        claim.get("fee_model"),
                        claim.get("root"),
                        claim.get("price_source"),
                        claim.get("price_asof"),
                        claim.get("price_confidence"),
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
        # Unknown net value is its own group, after known-value claims, so an
        # unavailable gas estimate can never masquerade as a profitable one.
        query += (
            " ORDER BY CASE status WHEN 'unclaimed' THEN 0 WHEN 'claimed' THEN 1 ELSE 2 END,"
            " CASE WHEN net_usd IS NULL THEN 1 ELSE 0 END, net_usd DESC"
        )
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
    @staticmethod
    def alert_key(
        kind: str,
        title: str,
        *,
        wallet: str | None = None,
        drop_id: str | None = None,
        tier: str | None = None,
    ) -> str:
        """Stable identity of an alert condition.

        ``tier`` lets a condition re-fire when it escalates (for example a
        deadline moving from 14 days to 3 days) without duplicating on every
        scan. Callers that do not pass a tier get the v1.1 behaviour.
        """
        parts = [kind, wallet or "", drop_id or "", title]
        if tier:
            parts.append(tier)
        return "|".join(parts)

    def add_alert(
        self,
        *,
        kind: str,
        severity: str,
        title: str,
        body: str,
        wallet: str | None = None,
        drop_id: str | None = None,
        snapshot_id: int | None = None,
        dedupe_key: str | None = None,
        tier: str | None = None,
    ) -> int | None:
        """Insert a new alert, or return ``None`` if the condition is already open."""
        key = dedupe_key or self.alert_key(kind, title, wallet=wallet, drop_id=drop_id, tier=tier)
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO alerts(created_at, kind, severity, title, body, wallet, drop_id, snapshot_id, dedupe_key, tier) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), kind, severity, redact_text(title), redact_text(body), wallet, drop_id, snapshot_id, key, tier),
            )
        return int(cursor.lastrowid) if cursor.rowcount else None

    def list_alerts(
        self,
        *,
        include_ack: bool = False,
        include_resolved: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        query = "SELECT * FROM alerts"
        clauses: list[str] = []
        if not include_ack:
            clauses.append("acknowledged = 0")
        if not include_resolved:
            clauses.append("resolved_at IS NULL")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += (
            " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,"
            " id DESC LIMIT ?"
        )
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

    def resolve_alert(self, alert_id: int, reason: str = "resolved by operator") -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE alerts SET resolved_at = ?, resolved_reason = ? "
                "WHERE id = ? AND resolved_at IS NULL",
                (now(), redact_text(reason), alert_id),
            )
        return cursor.rowcount > 0

    def reopen_alert(self, alert_id: int) -> bool:
        """Re-open a resolved alert.

        Raises ``sqlite3.IntegrityError`` when another open alert already holds
        the same dedupe key (the partial unique index protects the user from
        duplicate signals).
        """
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE alerts SET resolved_at = NULL, resolved_reason = NULL, "
                "acknowledged = 0 WHERE id = ? AND resolved_at IS NOT NULL",
                (alert_id,),
            )
        return cursor.rowcount > 0

    def resolve_stale_alerts(
        self,
        active_keys: set[str],
        *,
        kinds: Sequence[str],
        drop_ids: Sequence[str],
        reason: str = "condition cleared",
    ) -> int:
        """Resolve open condition alerts whose condition no longer holds.

        Event-style alerts are not passed in ``kinds`` and are therefore never
        swept away by a scan.  Rows are kept for history; only their state
        changes.
        """
        allowed = [kind for kind in kinds if kind]
        scoped = [drop for drop in drop_ids if drop]
        if not allowed or not scoped:
            return 0
        kind_marks = ",".join("?" for _ in allowed)
        drop_marks = ",".join("?" for _ in scoped)
        # Only conditions for drops evaluated by this scan are swept: a scan of
        # one chain must not silently resolve another chain's deadline alerts.
        query = (
            f"UPDATE alerts SET resolved_at = ?, resolved_reason = ? "
            f"WHERE resolved_at IS NULL AND kind IN ({kind_marks}) "
            f"AND drop_id IN ({drop_marks})"
        )
        parameters: list[Any] = [now(), redact_text(reason), *allowed, *scoped]
        if active_keys:
            query += " AND (dedupe_key IS NULL OR dedupe_key NOT IN (" + ",".join("?" for _ in active_keys) + "))"
            parameters.extend(sorted(active_keys))
        with self.connect() as connection:
            cursor = connection.execute(query, parameters)
        return int(cursor.rowcount)

    # ----------------------------------------------------------- notifications
    def add_notification(
        self, *, alert_id: int | None, kind: str, url_redacted: str
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO notifications(created_at, alert_id, kind, url_redacted, status, attempts) "
                "VALUES(?, ?, ?, ?, 'pending', 0)",
                (now(), alert_id, kind, url_redacted),
            )
        return int(cursor.lastrowid)

    def update_notification(
        self,
        notification_id: int,
        *,
        status: str,
        attempts: int,
        error: str | None = None,
        dead_letter: bool = False,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE notifications SET status = ?, attempts = ?, error = ?, dead_letter = ? "
                "WHERE id = ?",
                (status, attempts, redact_text(error) if error else None, 1 if dead_letter else 0, notification_id),
            )

    def list_notifications(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]

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
                (now(), actor, action, redact_text(detail)),
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


    # ------------------------------------------------------------- provenance
    def upsert_dataset_provenance(self, record: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO dataset_provenance(drop_id, filename, source_url, fetched_at, "
                "sha256, byte_size, root_verified, root, block_number, entry_count, tool_version) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(drop_id) DO UPDATE SET filename=excluded.filename, "
                "source_url=excluded.source_url, fetched_at=excluded.fetched_at, "
                "sha256=excluded.sha256, byte_size=excluded.byte_size, "
                "root_verified=excluded.root_verified, root=excluded.root, "
                "block_number=excluded.block_number, entry_count=excluded.entry_count, "
                "tool_version=excluded.tool_version",
                (
                    record["drop_id"],
                    record["filename"],
                    record.get("source_url"),
                    record["fetched_at"],
                    record["sha256"],
                    int(record.get("byte_size", 0)),
                    1 if record.get("root_verified") else 0,
                    record.get("root"),
                    record.get("block_number"),
                    int(record.get("entry_count", 0)),
                    record.get("tool_version", ""),
                ),
            )

    def dataset_provenance(self, drop_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_provenance WHERE drop_id = ?", (drop_id,)
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["root_verified"] = bool(payload["root_verified"])
        return payload

    # ------------------------------------------------------------- db admin
    def table_counts(self) -> dict[str, int]:
        """Row counts for the tables an operator cares about."""
        tables = (
            "wallets",
            "airdrops",
            "snapshots",
            "holdings",
            "claims",
            "alerts",
            "executions",
            "audit",
            "jobs",
            "notifications",
        )
        with self.connect() as connection:
            counts: dict[str, int] = {}
            for table in tables:
                try:
                    counts[table] = int(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    counts[table] = 0  # table added by a future/absent migration
            return counts

    def status(self) -> dict:
        """Read-only summary for ``idlescan db status``."""
        exists = self.path.is_file()
        size = self.path.stat().st_size if exists else 0
        current = self.schema_version() if exists else 0
        payload: dict[str, Any] = {
            "path": str(self.path),
            "exists": exists,
            "size_bytes": size,
            "schema_version": current,
            "latest_schema_version": LATEST_SCHEMA_VERSION,
            "pending_migrations": max(0, LATEST_SCHEMA_VERSION - current),
            "up_to_date": exists and current == LATEST_SCHEMA_VERSION,
            "counts": {},
            "latest_snapshot": None,
        }
        if exists and current:
            payload["counts"] = self.table_counts()
            latest = self.latest_snapshot()
            if latest:
                payload["latest_snapshot"] = {
                    "id": latest["id"],
                    "created_at": latest["created_at"],
                    "trigger": latest.get("trigger"),
                }
        return payload

    def check(self) -> dict:
        """Integrity and consistency checks; never modifies the database."""
        problems: list[str] = []
        if not self.path.is_file():
            return {
                "ok": False,
                "schema_version": 0,
                "latest_schema_version": LATEST_SCHEMA_VERSION,
                "checks": {},
                "problems": ["database file does not exist"],
            }
        with self.connect() as connection:
            current = schema_version(connection)
            integrity_rows = [
                str(row[0])
                for row in connection.execute("PRAGMA integrity_check").fetchall()
            ]
            integrity_ok = integrity_rows == ["ok"]
            if not integrity_ok:
                problems.extend(f"integrity: {row}" for row in integrity_rows[:20])

            foreign_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            for row in foreign_rows:
                problems.append(
                    f"foreign key violation: table={row[0]} rowid={row[1]} parent={row[2]}"
                )

            def scalar(query: str, parameters: Sequence[Any] = ()) -> int:
                return int(connection.execute(query, parameters).fetchone()[0])

            orphan_claims = scalar(
                "SELECT COUNT(*) FROM claims WHERE snapshot_id NOT IN (SELECT id FROM snapshots)"
            )
            orphan_holdings = scalar(
                "SELECT COUNT(*) FROM holdings WHERE snapshot_id NOT IN (SELECT id FROM snapshots)"
            )
            if orphan_claims:
                problems.append(f"{orphan_claims} claim row(s) reference a missing snapshot")
            if orphan_holdings:
                problems.append(f"{orphan_holdings} holding row(s) reference a missing snapshot")

            bad_snapshots = 0
            for row in connection.execute("SELECT id, totals, report FROM snapshots"):
                try:
                    json.loads(row["totals"])
                    json.loads(row["report"])
                except (TypeError, ValueError):
                    bad_snapshots += 1
            if bad_snapshots:
                problems.append(f"{bad_snapshots} snapshot(s) contain invalid JSON")

            if current > LATEST_SCHEMA_VERSION:
                problems.append(
                    f"schema v{current} is newer than this release (v{LATEST_SCHEMA_VERSION})"
                )
            elif current < LATEST_SCHEMA_VERSION:
                problems.append(
                    f"schema v{current} is behind v{LATEST_SCHEMA_VERSION}; run "
                    "`idlescan db migrate`"
                )

            if "dedupe_key" in {
                row[1] for row in connection.execute("PRAGMA table_info(alerts)")
            }:
                missing_keys = scalar(
                    "SELECT COUNT(*) FROM alerts WHERE dedupe_key IS NULL AND resolved_at IS NULL"
                )
                if missing_keys:
                    problems.append(f"{missing_keys} open alert(s) have no dedupe key")

            counts = {
                "snapshots": scalar("SELECT COUNT(*) FROM snapshots"),
                "claims": scalar("SELECT COUNT(*) FROM claims"),
                "holdings": scalar("SELECT COUNT(*) FROM holdings"),
                "alerts": scalar("SELECT COUNT(*) FROM alerts"),
            }
        return {
            "ok": not problems,
            "schema_version": current,
            "latest_schema_version": LATEST_SCHEMA_VERSION,
            "checks": {
                "integrity": integrity_ok,
                "foreign_keys": not foreign_rows,
                "orphans": not (orphan_claims or orphan_holdings),
                "snapshot_json": not bad_snapshots,
                "schema": current == LATEST_SCHEMA_VERSION,
                "counts": counts,
            },
            "problems": problems,
        }

    @staticmethod
    def _cutoff(days: int) -> str:
        moment = datetime.now(timezone.utc) - timedelta(days=days)
        return moment.replace(microsecond=0).isoformat()

    def _prune_plan(
        self,
        connection: sqlite3.Connection,
        *,
        snapshots: int,
        alerts_days: int,
        audit_days: int,
        executions_days: int,
        jobs_days: int,
    ) -> dict[str, int]:
        plan: dict[str, int] = {}

        if snapshots > 0:
            plan["snapshots"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE id NOT IN "
                    "(SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                    (snapshots,),
                ).fetchone()[0]
            )
            plan["holdings"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM holdings WHERE snapshot_id NOT IN "
                    "(SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                    (snapshots,),
                ).fetchone()[0]
            )
            plan["claims"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM claims WHERE snapshot_id NOT IN "
                    "(SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                    (snapshots,),
                ).fetchone()[0]
            )

        def age_count(table: str, days: int, extra: str = "") -> int:
            if days <= 0:
                return 0
            query = f"SELECT COUNT(*) FROM {table} WHERE created_at < ?"
            if extra:
                query += f" AND {extra}"
            return int(connection.execute(query, (self._cutoff(days),)).fetchone()[0])

        plan["alerts"] = age_count(
            "alerts", alerts_days, "acknowledged = 1 AND snapshot_id IS NULL"
        )
        plan["audit"] = age_count("audit", audit_days)
        plan["executions"] = age_count("executions", executions_days)
        plan["jobs"] = age_count("jobs", jobs_days, "finished_at IS NOT NULL")
        plan["notifications"] = age_count(
            "notifications", alerts_days, "status != 'pending'"
        )
        return {key: value for key, value in plan.items() if value}

    def prune(
        self,
        *,
        snapshots: int = 90,
        alerts_days: int = 180,
        audit_days: int = 365,
        executions_days: int = 365,
        jobs_days: int = 30,
        dry_run: bool = True,
        vacuum: bool = False,
    ) -> dict:
        """Delete history outside the retention windows.

        Dry-run by default: callers must opt in to deletion.  The newest
        ``snapshots`` snapshots and every open alert are always kept.
        """
        with self.connect() as connection:
            plan = self._prune_plan(
                connection,
                snapshots=max(0, snapshots),
                alerts_days=alerts_days,
                audit_days=audit_days,
                executions_days=executions_days,
                jobs_days=jobs_days,
            )
            if not dry_run and plan:
                if snapshots > 0:
                    keep = int(snapshots)
                    connection.execute(
                        "UPDATE alerts SET snapshot_id = NULL WHERE snapshot_id NOT IN "
                        "(SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                        (keep,),
                    )
                    connection.execute(
                        "DELETE FROM holdings WHERE snapshot_id NOT IN "
                        "(SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                        (keep,),
                    )
                    connection.execute(
                        "DELETE FROM claims WHERE snapshot_id NOT IN "
                        "(SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                        (keep,),
                    )
                    connection.execute(
                        "DELETE FROM snapshots WHERE id NOT IN "
                        "(SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                        (keep,),
                    )
                if alerts_days > 0:
                    cutoff = self._cutoff(alerts_days)
                    connection.execute(
                        "DELETE FROM alerts WHERE created_at < ? AND acknowledged = 1 "
                        "AND snapshot_id IS NULL",
                        (cutoff,),
                    )
                    connection.execute(
                        "DELETE FROM notifications WHERE created_at < ? AND status != 'pending'",
                        (cutoff,),
                    )
                if audit_days > 0:
                    connection.execute(
                        "DELETE FROM audit WHERE created_at < ?", (self._cutoff(audit_days),)
                    )
                if executions_days > 0:
                    connection.execute(
                        "DELETE FROM executions WHERE created_at < ?",
                        (self._cutoff(executions_days),),
                    )
                if jobs_days > 0:
                    connection.execute(
                        "DELETE FROM jobs WHERE created_at < ? AND finished_at IS NOT NULL",
                        (self._cutoff(jobs_days),),
                    )
        if vacuum and not dry_run:
            self.vacuum()
        return {
            "dry_run": dry_run,
            "deleted": plan,
            "vacuumed": bool(vacuum and not dry_run),
            "retention": {
                "snapshots": snapshots,
                "alerts_days": alerts_days,
                "audit_days": audit_days,
                "executions_days": executions_days,
                "jobs_days": jobs_days,
            },
        }
