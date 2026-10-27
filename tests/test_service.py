"""Service layer: demo seeding, write gate, alerts, registry risk, exports."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from idlescan.config import Settings
from idlescan.selftest import WALLET_CLAIMED, WALLET_EMPTY, WALLET_UNCLAIMED, MockRpc, build_world
from idlescan.service import (
    Conflict,
    NotFound,
    PlatformService,
    ServiceError,
    WriteDisabled,
    public_text,
)

TX = "0x" + "ab" * 32


class ServiceTestCase(unittest.TestCase):
    read_only = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            read_only=self.read_only,
            db_path=Path(self._tmp.name) / "platform.db",
        )
        self.service = PlatformService(
            self.settings,
            rpc_factory=lambda chain: MockRpc(build_world()),
        )
        self.service.seed_demo()

    def tearDown(self):
        self._tmp.cleanup()


class DemoSeedTests(ServiceTestCase):
    def test_demo_seeds_wallets_snapshot_and_claims(self):
        self.assertEqual(len(self.service.list_wallets()), 3)
        snapshot = self.service.db.latest_snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["trigger"], "demo")
        self.assertAlmostEqual(snapshot["totals"]["idle_usd"], 4697.888, places=2)
        self.assertAlmostEqual(snapshot["totals"]["unclaimed_usd"], 612.0, places=2)

        portfolio = self.service.portfolio()
        statuses = {row["wallet"]: row["status"] for row in portfolio["claims"]}
        self.assertEqual(statuses[WALLET_UNCLAIMED], "unclaimed")
        self.assertEqual(statuses[WALLET_CLAIMED], "claimed")
        self.assertEqual(statuses[WALLET_EMPTY], "no_allocation")

        unclaimed = next(row for row in portfolio["claims"] if row["status"] == "unclaimed")
        self.assertTrue(unclaimed["calldata"])
        self.assertAlmostEqual(unclaimed["net_usd"], 606.508114, places=3)
        self.assertEqual(unclaimed["wallet_label"], "Demo cold wallet")

    def test_demo_is_idempotent(self):
        before = self.service.db.latest_snapshot()["id"]
        self.service.seed_demo()
        self.assertEqual(self.service.db.latest_snapshot()["id"], before)
        self.assertEqual(len(self.service.list_wallets()), 3)

    def test_demo_scan_uses_mock_chain(self):
        self.service.settings.demo = True
        snapshot_id, payload = self.service.run_scan_sync(wallets=[WALLET_UNCLAIMED], trigger="test")
        self.assertGreater(snapshot_id, 1)
        self.assertEqual(len(payload["claims"]), 3)
        self.assertTrue(all(claim["deadline"] for claim in payload["claims"]))

    def test_airdrop_risk_and_board(self):
        drops = {drop["id"]: drop for drop in self.service.airdrops()}
        self.assertIn("demo-merkle", drops)
        demo = drops["demo-merkle"]
        self.assertEqual(demo["risk"]["label"], "high")
        self.assertGreaterEqual(demo["risk"]["score"], 70)
        self.assertEqual(demo["unclaimed"], 1)
        self.assertAlmostEqual(demo["claimable_net_usd"], 606.508114, places=3)
        self.assertFalse(demo["has_dataset"])  # demo claims come from the mock chain
        uniswap = drops["uniswap-uni"]
        self.assertTrue(uniswap["has_dataset"])  # dataset configured, file still missing
        self.assertEqual(uniswap["risk"]["label"], "medium")

    def test_alerts_are_verifiable_signals(self):
        alerts = self.service.alerts()
        kinds = {alert["kind"] for alert in alerts}
        self.assertIn("deadline", kinds)
        self.assertIn("missing_dataset", kinds)
        critical = [alert for alert in alerts if alert["severity"] == "critical"]
        deadline_alert = next(alert for alert in critical if alert["kind"] == "deadline")
        self.assertIn("closing", deadline_alert["title"].lower())
        self.assertIn("closes", deadline_alert["body"].lower())
        acked = self.service.ack_all_alerts()
        self.assertGreater(acked, 0)
        self.assertEqual(self.service.alerts(), [])

    def test_stats_flags(self):
        stats = self.service.stats()
        self.assertTrue(stats["read_only"])
        self.assertFalse(stats["write_enabled"])
        self.assertEqual(stats["wallets"], 3)
        self.assertEqual(stats["chains"], 14)
        self.assertGreater(stats["tokens"], 0)

    def test_export_formats(self):
        filename, content_type, payload = self.service.export("json")
        self.assertTrue(filename.endswith(".json"))
        self.assertEqual(content_type, "application/json")
        self.assertIn(b"Demo Airdrop", payload)

        _, content_type, payload = self.service.export("csv")
        self.assertTrue(content_type.startswith("text/csv"))
        self.assertIn(b"Demo Airdrop", payload)

        _, content_type, payload = self.service.export("md")
        self.assertTrue(content_type.startswith("text/markdown"))
        self.assertIn(b"Idle asset", payload)

        with self.assertRaises(ServiceError):
            self.service.export("xml")

    def test_explorer_profiles_and_exports(self):
        detail = self.service.airdrop_detail("demo-merkle")
        self.assertEqual(detail["id"], "demo-merkle")
        self.assertEqual(detail["wallet_count"], 3)
        self.assertGreaterEqual(detail["proof"]["verified"], 1)
        self.assertEqual(detail["contract"]["token_decimals"], 18)
        self.assertIn("net_usd", detail["history"][0] if detail["history"] else {})
        with self.assertRaises(NotFound):
            self.service.airdrop_detail("missing")

        wallet = self.service.wallet_detail(WALLET_UNCLAIMED)
        self.assertEqual(wallet["wallet"]["address"], WALLET_UNCLAIMED)
        self.assertEqual(wallet["totals"]["unclaimed"], 1)
        self.assertAlmostEqual(wallet["totals"]["net_usd"], 606.508114, places=3)
        with self.assertRaises(NotFound):
            self.service.wallet_detail("0x" + "99" * 20)
        with self.assertRaises(ServiceError):
            self.service.wallet_detail("nope")

        for fmt in ("json", "csv", "md"):
            filename, content_type, payload = self.service.export_drop("demo-merkle", fmt)
            self.assertIn("demo-merkle", filename)
            self.assertTrue(content_type)
            if fmt == "csv":
                self.assertIn(WALLET_UNCLAIMED.encode(), payload)
            else:
                self.assertIn(b"Demo Airdrop", payload)
        with self.assertRaises(ServiceError):
            self.service.export_drop("demo-merkle", "xml")
        with self.assertRaises(NotFound):
            self.service.export_drop("missing", "json")

    def test_analytics_diff_search_and_calendar(self):
        analytics = self.service.analytics()
        self.assertEqual(analytics["totals"]["positions"], 3)
        self.assertEqual(analytics["totals"]["wallets"], 3)
        self.assertEqual(analytics["snapshot"]["id"], 1)
        self.assertIsNone(analytics["diff"]["previous"])
        self.assertIsNotNone(analytics["coverage"]["ratio"])

        diff = self.service.snapshot_diff()
        self.assertEqual(diff["changes"], [])
        self.assertIsNone(diff["delta"])

        self.service.run_scan_sync(wallets=[WALLET_UNCLAIMED], trigger="test")
        latest = self.service.db.latest_snapshot()["id"]
        diff = self.service.snapshot_diff(latest)
        self.assertEqual(diff["previous"]["id"], 1)
        self.assertIn("idle_usd", diff["delta"])
        with self.assertRaises(NotFound):
            self.service.snapshot_diff(99999)

        result = self.service.search("demo")
        self.assertTrue(result["drops"])
        self.assertEqual(result["drops"][0]["id"], "demo-merkle")
        self.assertTrue(result["wallets"])
        self.assertEqual(self.service.search("x")["drops"], [])
        self.assertFalse(self.service.search("0xdeadbeef")["transactions"])

        calendar = self.service.calendar_ics()
        self.assertIn(b"BEGIN:VCALENDAR", calendar)
        self.assertIn(b"SUMMARY:Airdrop deadline: Demo Airdrop", calendar)
        self.assertIn(b"TRIGGER:-P3D", calendar)
        self.assertIn(b"demo-merkle-deadline", calendar)
        for line in calendar.decode().split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)
        empty = self.service.calendar_ics("uniswap-uni")
        self.assertNotIn(b"demo-merkle-deadline", empty)
        self.assertIn(b"BEGIN:VCALENDAR", empty)

    def test_check_address_is_ephemeral(self):
        # This class seeds demo data but keeps the live scanner; flip the demo
        # flag so the check uses the synthetic ledger, as `serve --demo` does.
        self.service.settings.demo = True
        wallets_before = len(self.service.list_wallets())
        snapshot_before = self.service.db.latest_snapshot()["id"]

        result = self.service.check_address(WALLET_UNCLAIMED)
        self.assertEqual(result["address"], WALLET_UNCLAIMED)
        self.assertEqual(result["totals"]["unclaimed"], 1)
        self.assertAlmostEqual(result["totals"]["net_usd"], 606.508114, places=3)
        self.assertEqual(result["totals"]["positions"], 1)
        self.assertTrue(result["tracked"])
        self.assertEqual(len(result["claims"]), 1)
        self.assertEqual(result["claims"][0]["drop_id"], "demo-merkle")
        self.assertEqual(result["claims"][0]["phase"], "closing")

        claimed = self.service.check_address(WALLET_CLAIMED)
        self.assertEqual(claimed["totals"]["claimed"], 1)
        self.assertEqual(claimed["totals"]["unclaimed"], 0)

        empty = self.service.check_address(WALLET_EMPTY)
        self.assertEqual(empty["totals"]["positions"], 1)
        self.assertEqual(empty["totals"]["unclaimed"], 0)

        unknown = self.service.check_address("0x" + "99" * 20)
        self.assertEqual(unknown["claims"], [])
        self.assertEqual(unknown["totals"]["positions"], 0)
        self.assertFalse(unknown["tracked"])

        with self.assertRaises(ServiceError):
            self.service.check_address("not-an-address")

        self.assertEqual(len(self.service.list_wallets()), wallets_before)
        self.assertEqual(self.service.db.latest_snapshot()["id"], snapshot_before)

    def test_bulk_wallet_import_reports_rows(self):
        result = self.service.add_wallets([
            {"address": "0x" + "AB" * 20, "label": "Research", "tags": "defi"},
            {"address": "nope"},
            {"address": "0x" + "ab" * 20},
        ])
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(result["wallets"][0]["address"], "0x" + "ab" * 20)
        self.assertEqual(result["wallets"][0]["label"], "Research")

        existing = self.service.add_wallets([{"address": WALLET_UNCLAIMED, "label": "Renamed"}])
        self.assertEqual(existing["updated"], 1)
        self.assertEqual(self.service.db.get_wallet(WALLET_UNCLAIMED)["label"], "Renamed")

        with self.assertRaises(ServiceError):
            self.service.add_wallets([])
        with self.assertRaises(ServiceError):
            self.service.add_wallets(["0x" + "11" * 20] * 201)

    def test_alerts_do_not_expose_absolute_dataset_paths(self):
        for alert in self.service.alerts(include_ack=True):
            self.assertNotIn("/home/", alert["body"])
            self.assertNotIn("Expected at", alert["body"])

        # Older rows were written before paths were sanitised at the source.
        self.service.db.add_alert(
            kind="missing_dataset",
            severity="warning",
            title="Legacy alert",
            body="Expected at /srv/app/data/datasets/uniswap-uni.claims.json. Add it.",
        )
        legacy = next(row for row in self.service.alerts(include_ack=True) if row["title"] == "Legacy alert")
        self.assertEqual(
            legacy["body"],
            "Expected at uniswap-uni.claims.json. Add it.",
        )

        # Legitimate URLs are left alone.
        self.assertEqual(
            public_text("Docs: https://example.com/a/b.html and /srv/app/data/x.json"),
            "Docs: https://example.com/a/b.html and x.json",
        )

        self.service.db.log("system", "db.backup", "/srv/app/data/backups/snap.db")
        entry = next(row for row in self.service.audit() if row["detail"] == "snap.db")
        self.assertEqual(entry["action"], "db.backup")

    def test_backup_contains_wallets_and_claims(self):
        target = Path(self._tmp.name) / "backups" / "manual.db"
        path = self.service.backup(target)
        self.assertEqual(path, target)
        self.assertTrue(path.is_file())
        connection = sqlite3.connect(path)
        try:
            wallets = connection.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
            claims = connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(wallets, 3)
        self.assertEqual(claims, 3)
        self.assertIn("db.backup", [row["action"] for row in self.service.audit()])

    def test_wallet_crud_and_validation(self):
        wallet = self.service.add_wallet("0xABCDEF0000000000000000000000000000000000", " Hot ", "defi")
        self.assertEqual(wallet["address"], "0xabcdef0000000000000000000000000000000000")
        self.assertEqual(wallet["label"], "Hot")
        with self.assertRaises(ServiceError):
            self.service.add_wallet("not-an-address")
        self.assertTrue(self.service.delete_wallet(wallet["address"]))
        self.assertFalse(self.service.delete_wallet(wallet["address"]))

    def test_custom_airdrop_add_and_remove(self):
        drop = self.service.add_airdrop({
            "id": "custom-drop",
            "name": "Custom Drop",
            "chain": "ethereum",
            "distributor": "0x" + "dd" * 20,
            "token": "0x" + "aa" * 20,
            "links": {"docs": "https://example.com"},
        })
        self.assertEqual(drop["id"], "custom-drop")
        self.assertEqual(drop["source"], "custom")
        self.assertIn("no eligibility source", " ".join(drop["risk"]["reasons"]))
        self.service.delete_airdrop("custom-drop")
        self.assertNotIn("custom-drop", {row["id"] for row in self.service.airdrops()})
        with self.assertRaises(Conflict):
            self.service.delete_airdrop("uniswap-uni")
        with self.assertRaises(NotFound):
            self.service.set_airdrop_enabled("missing", False)

    def test_custom_airdrop_cannot_overwrite_registry_ids(self):
        payload = {
            "id": "uniswap-uni",
            "name": "Impostor (UNI)",
            "chain": "ethereum",
            "distributor": "0x" + "ee" * 20,
        }
        with self.assertRaises(Conflict) as ctx:
            self.service.add_airdrop(payload)
        self.assertIn("managed internally", str(ctx.exception))
        self.assertEqual(self.service.db.get_airdrop("uniswap-uni")["source"], "registry")

        with self.assertRaises(Conflict):
            self.service.add_airdrop({**payload, "id": "demo-merkle", "name": "Impostor (DEMO)"})

        custom = {**payload, "id": "mine", "name": "Mine (MINE)"}
        self.service.add_airdrop(custom)
        updated = self.service.add_airdrop({**custom, "name": "Mine v2 (MINE)"})
        self.assertEqual(updated["name"], "Mine v2 (MINE)")
        self.assertEqual(updated["source"], "custom")

    def test_scan_requires_wallets(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = PlatformService(Settings(db_path=Path(tmp) / "empty.db"))
            with self.assertRaises(ServiceError):
                service.start_scan([])
            with self.assertRaises(ServiceError):
                service.run_scan_sync(wallets=[])

    def test_read_only_prepare_and_confirm_are_refused(self):
        with self.assertRaises(WriteDisabled) as ctx:
            self.service.prepare_claim("demo-merkle", WALLET_UNCLAIMED)
        self.assertIn("IDLESCAN_READ_ONLY=false", str(ctx.exception))
        with self.assertRaises(WriteDisabled):
            self.service.confirm_claim(drop_id="demo-merkle", wallet=WALLET_UNCLAIMED, tx_hash=TX)
        self.assertEqual(self.service.executions(), [])

    def test_prepare_unknown_position(self):
        with self.assertRaises(WriteDisabled):
            self.service.prepare_claim("nope", WALLET_UNCLAIMED)


class WriteModeTests(ServiceTestCase):
    read_only = False

    def test_prepare_returns_unsigned_transaction(self):
        result = self.service.prepare_claim("demo-merkle", WALLET_UNCLAIMED)
        tx = result["transaction"]
        self.assertEqual(tx["from"], WALLET_UNCLAIMED)
        self.assertEqual(tx["to"], "0x" + "dd" * 20)
        self.assertEqual(tx["chainId"], "0x1")
        self.assertEqual(tx["value"], "0x0")
        self.assertTrue(tx["data"].startswith("0x"))
        self.assertNotIn("gas", tx)
        self.assertEqual(len(result["warnings"]), 3)
        execution = self.service.executions()[0]
        self.assertEqual(execution["status"], "prepared")
        self.assertIsNone(execution["tx_hash"])
        audit = [row["action"] for row in self.service.audit()]
        self.assertIn("claim.prepare", audit)

    def test_prepare_rejects_claimed_and_missing(self):
        with self.assertRaises(Conflict):
            self.service.prepare_claim("demo-merkle", WALLET_CLAIMED)
        with self.assertRaises(NotFound):
            self.service.prepare_claim("demo-merkle", "0x" + "99" * 20)
        with self.assertRaises(NotFound):
            service = PlatformService(Settings(read_only=False, db_path=Path(self._tmp.name) / "empty.db"))
            service.prepare_claim("demo-merkle", WALLET_UNCLAIMED)

    def test_confirm_validates_hash_and_records_submission(self):
        with self.assertRaises(ServiceError):
            self.service.confirm_claim(drop_id="demo-merkle", wallet=WALLET_UNCLAIMED, tx_hash="0x123")
        result = self.service.confirm_claim(drop_id="demo-merkle", wallet=WALLET_UNCLAIMED, tx_hash=TX)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["tx_hash"], TX)
        execution = self.service.executions()[0]
        self.assertEqual(execution["status"], "submitted")
        self.assertEqual(execution["tx_hash"], TX)


if __name__ == "__main__":
    unittest.main()
