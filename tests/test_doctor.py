"""``idlescan doctor``: offline deployment checks and honest exit codes."""

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from idlescan.cli import main
from idlescan.config import Settings
from idlescan.db import Database
from idlescan.doctor import FAIL, OK, WARN, _registry_checks, run_doctor
from idlescan.migrations import LATEST_SCHEMA_VERSION, SCHEMA_V1
from idlescan.registry import Airdrop, Eligibility


def run_cli(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    old_env = {}
    if env:
        for key, value in env.items():
            old_env[key] = os.environ.get(key)
            os.environ[key] = value
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return code, out.getvalue(), err.getvalue()


def make_v1(path: Path, *, schema: int = 1) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_V1)
        connection.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(schema),),
        )
        connection.commit()
    finally:
        connection.close()


def by_name(report):
    return {check["name"]: check for check in report["checks"]}


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "idlescan.db"

    def tearDown(self):
        self._tmp.cleanup()

    def settings(self, **overrides):
        values = {"db_path": self.path, "dataset_dir": None, "read_only": True}
        values.update(overrides)
        return Settings(**values)

    def test_clean_deployment_passes_offline(self):
        Database(self.path).init()
        report = run_doctor(self.settings())
        self.assertTrue(report["ok"], report["checks"])
        self.assertEqual(report["failures"], 0)
        checks = by_name(report)
        self.assertEqual(checks["runtime"]["status"], OK)
        self.assertEqual(checks["version"]["status"], OK)
        self.assertEqual(checks["imports"]["status"], OK)
        self.assertEqual(checks["database"]["status"], OK)
        self.assertEqual(checks["database-integrity"]["status"], OK)
        self.assertIn("offline", checks["network"]["detail"])
        self.assertFalse(report["online"])

    def test_offline_never_touches_the_network(self):
        Database(self.path).init()
        with mock.patch("idlescan.rpc.Rpc.block_number", side_effect=AssertionError("network used")):
            report = run_doctor(self.settings())
        self.assertTrue(report["ok"])
        self.assertIn("no RPC or HTTP request", by_name(report)["network"]["detail"])

    def test_online_probes_rpc_endpoints(self):
        Database(self.path).init()
        with mock.patch("idlescan.rpc.Rpc.block_number", return_value=19_000_000) as probe:
            report = run_doctor(self.settings(), online=True)
        self.assertTrue(probe.called)
        checks = by_name(report)
        self.assertTrue(report["online"])
        rpc_checks = [item for item in checks if item.startswith("rpc:")]
        self.assertTrue(rpc_checks)
        self.assertEqual(checks[rpc_checks[0]]["status"], OK)
        self.assertIn("19000000", checks[rpc_checks[0]]["detail"])

    def test_online_failure_is_reported_not_raised(self):
        Database(self.path).init()
        from idlescan.rpc import RpcError

        with mock.patch("idlescan.rpc.Rpc.block_number", side_effect=RpcError("down")):
            report = run_doctor(self.settings(), online=True)
        self.assertFalse(report["ok"])
        failed = [item for item in report["checks"] if item["status"] == FAIL]
        self.assertTrue(any(item["name"].startswith("rpc:") for item in failed))
        self.assertTrue(all("hint" in item for item in failed))

    def test_pending_migration_is_a_warning_not_a_failure(self):
        make_v1(self.path)
        report = run_doctor(self.settings())
        self.assertTrue(report["ok"])
        checks = by_name(report)
        self.assertEqual(checks["database"]["status"], WARN)
        self.assertIn("needs migration", checks["database"]["detail"])
        self.assertEqual(checks["database-integrity"]["status"], OK)

    def test_newer_schema_is_a_failure(self):
        make_v1(self.path, schema=LATEST_SCHEMA_VERSION + 3)
        report = run_doctor(self.settings())
        self.assertFalse(report["ok"])
        checks = by_name(report)
        self.assertEqual(checks["database"]["status"], FAIL)
        self.assertIn("newer", checks["database"]["detail"])

    def test_missing_database_is_a_warning_and_is_not_created(self):
        report = run_doctor(self.settings())
        self.assertTrue(report["ok"])
        self.assertEqual(by_name(report)["database"]["status"], WARN)
        self.assertFalse(self.path.exists())

    def test_doctor_does_not_migrate_or_write_rows(self):
        make_v1(self.path)
        before = Database(self.path).table_counts()
        report = run_doctor(self.settings())
        self.assertTrue(report["ok"])
        self.assertEqual(Database(self.path).schema_version(), 1)
        self.assertEqual(Database(self.path).table_counts(), before)
        self.assertEqual(list(self.path.parent.glob("*.bak")), [])

    def test_unsafe_remote_bind_fails_configuration(self):
        report = run_doctor(self.settings(host="0.0.0.0"))
        self.assertFalse(report["ok"])
        self.assertEqual(by_name(report)["configuration"]["status"], FAIL)
        self.assertIn("auth", by_name(report)["configuration"]["detail"].lower())

    def test_remote_bind_with_token_passes(self):
        Database(self.path).init()
        report = run_doctor(self.settings(host="0.0.0.0", auth_token="t" * 32))
        self.assertTrue(report["ok"])
        self.assertEqual(by_name(report)["auth"]["status"], OK)

    def test_allow_http_fetch_is_flagged(self):
        Database(self.path).init()
        report = run_doctor(self.settings(allow_http_fetch=True))
        self.assertEqual(by_name(report)["fetch-policy"]["status"], WARN)

    def test_corrupt_database_fails_integrity(self):
        Database(self.path).init()
        with Database(self.path).connect() as connection:
            connection.execute(
                "INSERT INTO claims(snapshot_id, drop_id, name, chain, wallet, "
                "distributor, status) "
                "VALUES(999, 'x', 'X', 'ethereum', ?, ?, 'unclaimed')",
                ("0x" + "11" * 20, "0x" + "22" * 20),
            )
        report = run_doctor(self.settings())
        self.assertFalse(report["ok"])
        integrity = by_name(report)["database-integrity"]
        self.assertEqual(integrity["status"], FAIL)
        self.assertIn("orphans", integrity["detail"])

    def test_unknown_selected_chain_fails(self):
        Database(self.path).init()
        report = run_doctor(self.settings(chains=["not-a-chain"]))
        self.assertFalse(report["ok"])
        self.assertEqual(by_name(report)["selected-chains"]["status"], FAIL)

    def test_missing_dataset_is_reported(self):
        drop = Airdrop(
            id="test",
            name="Test",
            chain="ethereum",
            distributor="0x" + "dd" * 20,
            token=None,
            eligibility=Eligibility(type="merkle_dataset", dataset=str(self.root / "missing.json")),
        )
        with mock.patch("idlescan.doctor.load_registry", return_value=[drop]):
            checks = _registry_checks(self.settings(), object())
        datasets = next(item for item in checks if item.name == "datasets")
        self.assertEqual(datasets.status, WARN)
        self.assertIn("missing.json", datasets.detail)

    def test_stale_dataset_is_reported(self):
        dataset = self.root / "claims.json"
        dataset.write_text("{}")
        old = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
        Path(dataset).touch()
        import os

        os.utime(dataset, (old, old))
        drop = Airdrop(
            id="test",
            name="Test",
            chain="ethereum",
            distributor="0x" + "dd" * 20,
            token=None,
            eligibility=Eligibility(type="merkle_dataset", dataset=str(dataset)),
        )
        with mock.patch("idlescan.doctor.load_registry", return_value=[drop]):
            checks = _registry_checks(self.settings(dataset_stale_days=30), object())
        datasets = next(item for item in checks if item.name == "datasets")
        self.assertEqual(datasets.status, WARN)
        self.assertIn("stale", datasets.detail.lower())

    def test_fresh_dataset_is_ok(self):
        dataset = self.root / "claims.json"
        dataset.write_text("{}")
        drop = Airdrop(
            id="test",
            name="Test",
            chain="ethereum",
            distributor="0x" + "dd" * 20,
            token=None,
            eligibility=Eligibility(type="merkle_dataset", dataset=str(dataset)),
        )
        with mock.patch("idlescan.doctor.load_registry", return_value=[drop]):
            checks = _registry_checks(self.settings(), object())
        datasets = next(item for item in checks if item.name == "datasets")
        self.assertEqual(datasets.status, OK)


class DoctorCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "idlescan.db"
        Database(self.path).init()

    def tearDown(self):
        self._tmp.cleanup()

    def test_json_output_and_exit_code(self):
        code, out, _ = run_cli(
            "doctor",
            "--json",
            "--db",
            str(self.path),
            env={"IDLESCAN_READ_ONLY": "true"},
        )
        # A fresh deployment has a missing-dataset warning, so exit 1.
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["warnings"], 1)
        self.assertEqual(payload["version"], "1.2.0")

    def test_text_output_lists_checks(self):
        code, out, _ = run_cli(
            "doctor",
            "--db",
            str(self.path),
            env={"IDLESCAN_READ_ONLY": "true"},
        )
        self.assertEqual(code, 1)
        self.assertIn("database", out)
        self.assertIn("imports", out)
        self.assertIn("warning(s)", out)

    def test_newer_schema_exits_non_zero(self):
        newer = Path(self._tmp.name) / "newer.db"
        make_v1(newer, schema=LATEST_SCHEMA_VERSION + 1)
        code, out, _ = run_cli("doctor", "--db", str(newer))
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertIn("newer", out)

    def test_missing_env_file_is_a_usage_error(self):
        code, _, err = run_cli("doctor", "--env-file", str(Path(self._tmp.name) / "nope.env"))
        self.assertEqual(code, 2)
        self.assertIn("not found", err)


if __name__ == "__main__":
    unittest.main()
