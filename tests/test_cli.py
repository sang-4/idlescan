"""CLI surface: no-network commands, argument validation, exit codes."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from idlescan.cli import main, read_addresses


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_no_addresses_is_a_usage_error(self):
        code, out, err = run_cli("scan")
        self.assertEqual(code, 2)
        self.assertIn("no addresses", err)

    def test_invalid_address_rejected(self):
        code, _, err = run_cli("scan", "0xnot-an-address")
        self.assertEqual(code, 2)
        self.assertIn("not a valid EVM address", err)

    def test_chains_listing(self):
        code, out, _ = run_cli("chains")
        self.assertEqual(code, 0)
        self.assertIn("ethereum", out)
        self.assertIn("arbitrum", out)

    def test_chains_json(self):
        code, out, _ = run_cli("chains", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(any(c["key"] == "base" for c in payload))
        self.assertTrue(all(c["rpcs"] for c in payload))

    def test_tokens_listing(self):
        code, out, _ = run_cli("tokens", "--chain", "ethereum")
        self.assertEqual(code, 0)
        self.assertIn("USDC", out)

    def test_selftest_json(self):
        code, out, _ = run_cli("selftest", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        statuses = {c["wallet"]: c["status"] for c in payload["claims"]}
        self.assertIn("unclaimed", statuses.values())
        self.assertIn("claimed", statuses.values())

    def test_selftest_markdown(self):
        code, out, _ = run_cli("selftest", "--markdown")
        self.assertEqual(code, 0)
        self.assertIn("# Idle asset & unclaimed airdrop report", out)

    def test_unknown_chain_is_config_error(self):
        code, _, err = run_cli("scan", "0x" + "11" * 20, "--chains", "atlantis")
        self.assertEqual(code, 2)
        self.assertIn("unknown chain", err)

    def test_dataset_command_validates_proofs_offline(self):
        import json as _json

        source = Path(__file__).resolve().parent.parent / "examples" / "claims.example.json"
        payload = _json.loads(source.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claims.json"
            path.write_text(_json.dumps(payload))
            code, out, _ = run_cli(
                "dataset", str(path),
                "--root", payload["merkleRoot"],
                "--chain", "ethereum",
                "--distributor", "0x090D4613473dEE047c3f2706764f49E0821D256e",
            )
            self.assertEqual(code, 0)
            self.assertIn("proofs verified", out)

            # Corrupt a proof: the command must fail loudly instead of trusting it.
            broken = _json.loads(_json.dumps(payload))
            first = next(iter(broken["claims"]))
            broken["claims"][first]["proof"][0] = "0x" + "00" * 32
            path.write_text(_json.dumps(broken))
            code, out, _ = run_cli(
                "dataset", str(path),
                "--root", payload["merkleRoot"],
                "--chain", "ethereum",
                "--distributor", "0x090D4613473dEE047c3f2706764f49E0821D256e",
            )
            self.assertEqual(code, 1)
            self.assertIn("FAIL", out)

    def test_dataset_command_missing_file(self):
        code, _, err = run_cli(
            "dataset", "/nonexistent/claims.json",
            "--root", "0x" + "11" * 32,
            "--chain", "ethereum",
            "--distributor", "0x090D4613473dEE047c3f2706764f49E0821D256e",
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_read_addresses_dedupes_and_strips_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "addresses.txt"
            path.write_text(
                "# my wallets\n"
                "0x" + "11" * 20 + "  # cold\n"
                "0x" + "11" * 20 + "\n"
                "\n"
                "0x" + "22" * 20 + ", 0x" + "33" * 20 + "\n"
            )
            addresses = read_addresses([], path)
            self.assertEqual(len(addresses), 3)
            self.assertEqual(addresses[0], "0x" + "11" * 20)


if __name__ == "__main__":
    unittest.main()


class PlatformCliTests(unittest.TestCase):
    """Database-backed commands: wallets, claims, alerts, export."""

    def setUp(self):
        from idlescan.config import Settings
        from idlescan.service import PlatformService

        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "cli.db"
        self.service = PlatformService(Settings(db_path=self.db))
        self.service.seed_demo()

    def tearDown(self):
        self._tmp.cleanup()

    def test_wallets_list_shows_labels_and_addresses(self):
        code, out, err = run_cli("wallets", "list", "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertIn("Demo cold wallet", out)
        self.assertIn("0x1111111111111111111111111111111111111111", out)

    def test_wallets_add_and_remove(self):
        address = "0x" + "ab" * 20
        code, out, err = run_cli("wallets", "add", address, "--label", "Research", "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertIn("tracking", out)
        code, out, _ = run_cli("wallets", "list", "--db", str(self.db))
        self.assertIn("Research", out)
        code, out, err = run_cli("wallets", "rm", address, "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertIn("stopped tracking", out)

    def test_wallets_add_rejects_invalid_address(self):
        code, _, err = run_cli("wallets", "add", "nope", "--db", str(self.db))
        self.assertEqual(code, 1)
        self.assertIn("not a valid EVM address", err)

    def test_check_address_reports_without_saving(self):
        env = Path(self._tmp.name) / "demo.env"
        env.write_text("IDLESCAN_DEMO=true\n")
        code, out, err = run_cli(
            "check", "0x1111111111111111111111111111111111111111",
            "--db", str(self.db), "--env-file", str(env),
        )
        self.assertEqual(code, 0, err)
        self.assertIn("nothing was saved", out)
        self.assertIn("demo-merkle", out)
        self.assertIn("$606.51", out)
        self.assertIn("tracked", out)

        code, out, err = run_cli(
            "check", "0x1111111111111111111111111111111111111111",
            "--json", "--db", str(self.db), "--env-file", str(env),
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["totals"]["unclaimed"], 1)

        code, _, err = run_cli(
            "check", "not-an-address", "--db", str(self.db), "--env-file", str(env)
        )
        self.assertEqual(code, 1)
        self.assertIn("not a valid EVM address", err)

    def test_claims_list_and_json(self):
        code, out, err = run_cli("claims", "list", "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertIn("demo-merkle", out)
        self.assertIn("unclaimed", out)
        self.assertIn("verified", out)

        code, out, _ = run_cli("claims", "list", "--json", "--db", str(self.db))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["claims"][0]["drop_id"], "demo-merkle")
        self.assertTrue(all(row["status"] == "unclaimed" for row in payload["claims"]))

    def test_claims_list_requires_a_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run_cli("claims", "list", "--db", str(Path(tmp) / "empty.db"))
        self.assertEqual(code, 1)
        self.assertIn("no snapshot", err)

    def test_claim_prepare_is_gated_in_read_only_mode(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"IDLESCAN_READ_ONLY": "true"}):
            code, _, err = run_cli(
                "claims", "prepare", "demo-merkle",
                "0x1111111111111111111111111111111111111111",
                "--db", str(self.db),
            )
        self.assertEqual(code, 1)
        self.assertIn("read-only mode", err)
        self.assertIn("IDLESCAN_READ_ONLY=false", err)

    def test_alerts_list_and_ack_all(self):
        code, out, err = run_cli("alerts", "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertIn("deadline", out.lower())
        code, out, _ = run_cli("alerts", "--ack-all", "--db", str(self.db))
        self.assertEqual(code, 0)
        self.assertIn("acknowledged", out)
        code, out, _ = run_cli("alerts", "--db", str(self.db))
        self.assertEqual(code, 0)
        self.assertIn("no open alerts", out)

    def test_export_writes_a_file(self):
        target = Path(self._tmp.name) / "report.json"
        code, _, err = run_cli("export", "--format", "json", "--out", str(target), "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertTrue(target.exists())
        payload = json.loads(target.read_text())
        self.assertEqual(payload["claims"][0]["drop_id"], "demo-merkle")

    def test_scan_saved_needs_wallets(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run_cli("scan", "--saved", "--db", str(Path(tmp) / "empty.db"))
        self.assertEqual(code, 2)
        self.assertIn("no addresses", err)

    def test_backup_writes_a_readable_copy(self):
        import sqlite3

        target = Path(self._tmp.name) / "backups" / "cli-copy.db"
        code, out, err = run_cli("backup", "--out", str(target), "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertTrue(target.is_file())
        self.assertIn("backup written", out)
        connection = sqlite3.connect(target)
        try:
            wallets = connection.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
            claims = connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(wallets, 3)
        self.assertEqual(claims, 3)

        code, out, err = run_cli("backup", "--out", str(Path(self._tmp.name) / "b2.db"), "--json", "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["path"].endswith("b2.db"))

    def test_calendar_writes_ics(self):
        target = Path(self._tmp.name) / "deadlines.ics"
        code, out, err = run_cli("calendar", "--out", str(target), "--db", str(self.db))
        self.assertEqual(code, 0, err)
        self.assertTrue(target.exists())
        content = target.read_text()
        self.assertIn("BEGIN:VCALENDAR", content)
        self.assertIn("Demo Airdrop", content)
        self.assertIn("TRIGGER:-P3D", content)

        code, out, _ = run_cli("calendar", "--drop", "demo-merkle", "--db", str(self.db))
        self.assertEqual(code, 0)
        self.assertIn("BEGIN:VEVENT", out)
