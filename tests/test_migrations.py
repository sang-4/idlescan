"""Schema migrations, database admin, retention and backup rotation."""

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from idlescan.cli import main
from idlescan.config import Settings
from idlescan.db import Database
from idlescan.migrations import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    SCHEMA_V1,
    BackupFailed,
    Migration,
    MigrationError,
    SchemaTooNew,
    apply_migrations,
    schema_version,
)
from idlescan.service import PlatformService

WALLET = "0x" + "11" * 20


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def make_v1(path: Path, *, schema: int = 1) -> None:
    """Create a database exactly as idlescan v1.1 left it, with sample rows."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_V1)
        connection.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(schema),))
        connection.execute(
            "INSERT INTO wallets(address, label, tags, archived, created_at) "
            "VALUES(?, 'old wallet', 'cold', 0, '2026-01-01T00:00:00+00:00')",
            (WALLET,),
        )
        connection.execute(
            "INSERT INTO snapshots(created_at, chains, wallets, trigger, totals, report) "
            "VALUES('2026-01-01T00:00:00+00:00', '[\"ethereum\"]', ?, 'manual', '{}', '{}')",
            (json.dumps([WALLET]),),
        )
        snapshot_id = connection.execute("SELECT id FROM snapshots").fetchone()[0]
        connection.execute(
            "INSERT INTO holdings(snapshot_id, chain, wallet, asset, decimals, raw) "
            "VALUES(?, 'ethereum', ?, 'ETH', 18, '1')",
            (snapshot_id, WALLET),
        )
        connection.execute(
            "INSERT INTO claims(snapshot_id, drop_id, name, chain, wallet, distributor, status) "
            "VALUES(?, 'legacy', 'Legacy drop', 'ethereum', ?, ?, 'unclaimed')",
            (snapshot_id, WALLET, "0x" + "22" * 20),
        )
        connection.execute(
            "INSERT INTO alerts(created_at, kind, severity, title, body, wallet, drop_id, snapshot_id) "
            "VALUES('2026-01-01T00:00:00+00:00', 'deadline', 'warning', 'Old alert', 'body', ?, 'legacy', ?)",
            (WALLET, snapshot_id),
        )
        connection.execute(
            "INSERT INTO audit(created_at, actor, action, detail) "
            "VALUES('2026-01-01T00:00:00+00:00', 'cli', 'scan', 'old')"
        )
        connection.commit()
    finally:
        connection.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "idlescan.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_database_is_at_latest_version(self):
        db = Database(self.path).init()
        self.assertEqual(db.schema_version(), LATEST_SCHEMA_VERSION)
        self.assertEqual(db.applied_migrations, [1, 2])

    def test_v1_database_upgrades_and_preserves_rows(self):
        make_v1(self.path)
        db = Database(self.path).init()
        self.assertEqual(db.schema_version(), LATEST_SCHEMA_VERSION)
        self.assertEqual(db.applied_migrations, [2])
        self.assertEqual(len(db.list_wallets()), 1)
        self.assertEqual(len(db.claims(1)), 1)
        claim = db.claim(1, "legacy", WALLET)
        self.assertIsNotNone(claim)
        self.assertIn("gas_estimate_quality", claim)
        self.assertIsNone(claim["gas_estimate_quality"])
        self.assertEqual(db.list_alerts()[0]["dedupe_key"], "deadline|" + WALLET + "|legacy|Old alert")
        self.assertEqual(db.list_audit()[0]["detail"], "old")

    def test_v1_upgrade_writes_a_restorable_backup(self):
        make_v1(self.path)
        Database(self.path).init()
        backup = self.path.with_name(self.path.name + ".pre-v1.bak")
        self.assertTrue(backup.is_file())
        with sqlite3.connect(backup) as connection:
            self.assertEqual(schema_version(connection), 1)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(claims)")}
            self.assertNotIn("gas_units", columns)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM wallets").fetchone()[0], 1)

    def test_newer_schema_is_refused(self):
        make_v1(self.path, schema=LATEST_SCHEMA_VERSION + 5)
        with self.assertRaises(SchemaTooNew):
            Database(self.path).init()

    def test_backup_failure_blocks_the_migration(self):
        make_v1(self.path)
        blocker = self.path.with_name(self.path.name + ".pre-v1.bak")
        blocker.mkdir()
        with self.assertRaises(BackupFailed) as ctx:
            Database(self.path).init()
        self.assertIn("refusing to migrate", str(ctx.exception))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(schema_version(connection), 1)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(claims)")}
            self.assertNotIn("gas_units", columns)

    def test_failed_migration_rolls_back_and_keeps_version(self):
        make_v1(self.path)

        def explode(connection):
            connection.execute("ALTER TABLE claims ADD COLUMN half_applied TEXT")
            raise RuntimeError("boom")

        extra = Migration(3, "deliberately broken", explode)
        with mock.patch("idlescan.migrations.MIGRATIONS", MIGRATIONS + (extra,)):
            with self.assertRaises(MigrationError) as ctx:
                apply_migrations(sqlite3.connect(self.path), self.path)
        self.assertIn("migration 3", str(ctx.exception))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(schema_version(connection), 2)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(claims)")}
            self.assertNotIn("half_applied", columns)

    def test_migration_is_idempotent(self):
        make_v1(self.path)
        first = Database(self.path).init()
        second = Database(self.path).init()
        self.assertEqual(first.applied_migrations, [2])
        self.assertEqual(second.applied_migrations, [])


class CheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "idlescan.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_check_passes_on_a_migrated_database(self):
        db = Database(self.path).init()
        result = db.check()
        self.assertTrue(result["ok"], result["problems"])
        self.assertTrue(result["checks"]["integrity"])
        self.assertTrue(result["checks"]["orphans"])
        self.assertEqual(result["schema_version"], LATEST_SCHEMA_VERSION)

    def test_check_reports_orphans_and_bad_json(self):
        db = Database(self.path).init()
        with db.connect() as connection:
            connection.execute(
                "INSERT INTO claims(snapshot_id, drop_id, name, chain, wallet, distributor, status) "
                "VALUES(999, 'x', 'X', 'ethereum', ?, ?, 'unclaimed')",
                (WALLET, "0x" + "22" * 20),
            )
            connection.execute(
                "INSERT INTO snapshots(created_at, chains, wallets, trigger, totals, report) "
                "VALUES('2026-01-01T00:00:00+00:00', '[]', '[]', 'manual', 'not json', '{}')"
            )
        result = db.check()
        self.assertFalse(result["ok"])
        self.assertTrue(any("missing snapshot" in item for item in result["problems"]))
        self.assertTrue(any("invalid JSON" in item for item in result["problems"]))

    def test_check_reports_a_missing_file(self):
        result = Database(self.path).check()
        self.assertFalse(result["ok"])
        self.assertIn("does not exist", result["problems"][0])

    def test_status_before_migration_reports_pending(self):
        make_v1(self.path)
        payload = Database(self.path).status()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["pending_migrations"], LATEST_SCHEMA_VERSION - 1)
        self.assertFalse(payload["up_to_date"])

    def test_status_reports_counts_and_latest_snapshot(self):
        db = Database(self.path).init()
        db.save_snapshot(
            chains=["ethereum"],
            wallets=[WALLET],
            totals={},
            report={"claims": []},
            trigger="manual",
        )
        payload = db.status()
        self.assertTrue(payload["up_to_date"])
        self.assertEqual(payload["counts"]["snapshots"], 1)
        self.assertEqual(payload["latest_snapshot"]["id"], 1)


class PruneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "idlescan.db").init()

    def tearDown(self):
        self._tmp.cleanup()

    def add_snapshot(self) -> int:
        return self.db.save_snapshot(
            chains=["ethereum"],
            wallets=[WALLET],
            totals={},
            report={
                "claims": [
                    {
                        "drop_id": "d",
                        "name": "D",
                        "chain": "ethereum",
                        "wallet": WALLET,
                        "distributor": "0x" + "22" * 20,
                        "status": "unclaimed",
                    }
                ],
                "holdings": [
                    {
                        "chain": "ethereum",
                        "wallet": WALLET,
                        "asset": "ETH",
                        "decimals": 18,
                        "raw": "1",
                    }
                ],
            },
        )

    def test_dry_run_reports_but_does_not_delete(self):
        for _ in range(3):
            self.add_snapshot()
        before = self.db.table_counts()
        plan = self.db.prune(snapshots=1, dry_run=True)
        self.assertEqual(plan["deleted"]["snapshots"], 2)
        self.assertEqual(self.db.table_counts(), before)

    def test_prune_keeps_newest_snapshot_and_children(self):
        first = self.add_snapshot()
        latest = self.add_snapshot()
        plan = self.db.prune(snapshots=1, dry_run=False)
        self.assertEqual(plan["deleted"]["snapshots"], 1)
        self.assertEqual([row["id"] for row in self.db.list_snapshots()], [latest])
        self.assertEqual(len(self.db.claims(latest)), 1)
        self.assertEqual(len(self.db.holdings(latest)), 1)
        self.assertEqual(self.db.claims(first), [])
        self.assertEqual(self.db.holdings(first), [])

    def test_open_alerts_and_latest_snapshot_are_retained(self):
        snapshot = self.add_snapshot()
        self.db.add_alert(kind="deadline", severity="warning", title="Open", body="b", snapshot_id=snapshot)
        with self.db.connect() as connection:
            connection.execute("UPDATE alerts SET created_at = '2020-01-01T00:00:00+00:00'")
        self.db.prune(snapshots=1, alerts_days=1, dry_run=False)
        self.assertEqual(len(self.db.list_alerts()), 1)
        self.assertEqual(self.db.list_alerts()[0]["snapshot_id"], snapshot)

    def test_old_acknowledged_detached_alerts_are_deleted(self):
        self.add_snapshot()
        self.db.add_alert(kind="deadline", severity="warning", title="Ack me", body="b", snapshot_id=None)
        alert_id = self.db.list_alerts()[0]["id"]
        self.db.ack_alert(alert_id)
        with self.db.connect() as connection:
            connection.execute("UPDATE alerts SET created_at = '2020-01-01T00:00:00+00:00'")
            connection.execute(
                "INSERT INTO audit(created_at, actor, action, detail) "
                "VALUES('2020-01-01T00:00:00+00:00', 'cli', 'old', 'x')"
            )
        plan = self.db.prune(snapshots=1, alerts_days=1, audit_days=1, dry_run=False)
        self.assertEqual(plan["deleted"].get("alerts"), 1)
        self.assertGreaterEqual(plan["deleted"].get("audit", 0), 1)
        self.assertEqual(self.db.list_alerts(include_ack=True), [])
        self.assertTrue(all(row["action"] != "old" for row in self.db.list_audit()))

    def test_zero_retention_keeps_everything(self):
        for _ in range(2):
            self.add_snapshot()
        plan = self.db.prune(snapshots=0, alerts_days=0, audit_days=0, executions_days=0, jobs_days=0)
        self.assertEqual(plan["deleted"], {})
        self.assertEqual(len(self.db.list_snapshots()), 2)


class BackupRotationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=Path(self._tmp.name) / "idlescan.db", backup_keep=2)
        self.service = PlatformService(self.settings)
        self.service.db.init()
        self.directory = Path(self._tmp.name) / "backups"
        self.directory.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_rotation_keeps_newest_named_backups(self):
        for index in range(4):
            path = self.directory / f"idlescan-2026010{index + 1}-000000.db"
            path.write_bytes(b"backup")
        removed = self.service._rotate_backups(self.directory)
        remaining = sorted(item.name for item in self.directory.iterdir())
        self.assertEqual(len(removed), 2)
        self.assertEqual(
            remaining,
            ["idlescan-20260103-000000.db", "idlescan-20260104-000000.db"],
        )

    def test_backup_into_a_directory_rotates(self):
        for _ in range(3):
            path = self.service.backup(self.directory)
            self.assertTrue(path.is_file())
        self.assertLessEqual(len(list(self.directory.glob("idlescan-*.db"))), 2)

    def test_explicit_file_target_is_not_rotated(self):
        target = Path(self._tmp.name) / "keep-me.db"
        self.service.backup(target)
        self.service.backup(target)
        self.assertTrue(target.is_file())


class DatabaseCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "idlescan.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_status_json_on_a_fresh_upgrade(self):
        make_v1(self.path)
        code, out, _ = run_cli("db", "status", "--json", "--db", str(self.path))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["pending_migrations"], LATEST_SCHEMA_VERSION - 1)

    def test_migrate_then_status(self):
        make_v1(self.path)
        code, out, _ = run_cli("db", "migrate", "--json", "--db", str(self.path))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["from_version"], 1)
        self.assertEqual(payload["to_version"], LATEST_SCHEMA_VERSION)
        self.assertIn(2, payload["applied"])
        self.assertTrue(Path(payload["backup"]).is_file())
        code, out, _ = run_cli("db", "status", "--json", "--db", str(self.path))
        self.assertTrue(json.loads(out)["up_to_date"])

    def test_migrate_is_idempotent_from_cli(self):
        run_cli("db", "migrate", "--db", str(self.path))
        code, out, _ = run_cli("db", "migrate", "--db", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("already at schema", out)

    def test_check_exit_code(self):
        code, _, err = run_cli("db", "check", "--db", str(self.path))
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)

    def test_check_passes_after_migration(self):
        run_cli("db", "migrate", "--db", str(self.path))
        code, out, _ = run_cli("db", "check", "--json", "--db", str(self.path))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_prune_defaults_to_dry_run_then_applies(self):
        Database(self.path).init()
        for _ in range(3):
            with Database(self.path).connect() as connection:
                connection.execute(
                    "INSERT INTO snapshots(created_at, chains, wallets, trigger, totals, report) "
                    "VALUES('2026-01-01T00:00:00+00:00', '[]', '[]', 'manual', '{}', '{}')"
                )
        code, out, _ = run_cli("db", "prune", "--snapshots", "1", "--db", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("dry run", out)
        self.assertEqual(len(Database(self.path).list_snapshots()), 3)
        code, out, _ = run_cli("db", "prune", "--snapshots", "1", "--yes", "--db", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("deleted", out)
        self.assertEqual(len(Database(self.path).list_snapshots()), 1)


if __name__ == "__main__":
    unittest.main()
