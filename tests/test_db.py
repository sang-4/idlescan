"""SQLite persistence: idempotent schema, CRUD, snapshots, alerts, audit."""

import tempfile
import unittest
from pathlib import Path

from idlescan.db import SCHEMA_VERSION, Database


def report_payload(
    *, idle=100.0, unclaimed=25.0, status="unclaimed", wallet="0x1111111111111111111111111111111111111111"
):
    return {
        "generated_at": "2026-10-26T00:00:00+00:00",
        "wallets": [wallet],
        "chains": ["ethereum"],
        "totals": {"idle_usd": idle, "unclaimed_usd": unclaimed, "combined_usd": idle + unclaimed},
        "holdings": [
            {
                "chain": "ethereum",
                "chain_id": 1,
                "wallet": wallet,
                "asset": "ETH",
                "asset_address": None,
                "decimals": 18,
                "raw": str(10**18),
                "usd_price": 2000.0,
                "usd_value": 2000.0,
            }
        ],
        "claims": [
            {
                "drop_id": "demo-drop",
                "name": "Demo Drop",
                "chain": "ethereum",
                "wallet": wallet,
                "distributor": "0x" + "dd" * 20,
                "token": "0x" + "aa" * 20,
                "status": status,
                "expired": False,
                "deadline": "2026-12-01",
                "amount_raw": str(10**18),
                "decimals": 18,
                "usd_price": 2.0,
                "usd_value": unclaimed,
                "gas_native": 0.001,
                "gas_usd": 2.0,
                "net_usd": unclaimed - 2.0,
                "proof_verified": True,
                "eligibility_source": "test",
                "note": None,
                "claim_calldata": "0xdeadbeef",
            }
        ],
        "warnings": [],
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "test.db"
        self.db = Database(self.path).init()

    def tearDown(self):
        self._tmp.cleanup()

    def test_init_is_idempotent_and_records_version(self):
        Database(self.path).init()
        with self.db.connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        self.assertEqual(row["value"], str(SCHEMA_VERSION))

    def test_wallets(self):
        wallet = self.db.add_wallet("0xABCDEF0000000000000000000000000000000000", "Cold", "old,whale")
        self.assertEqual(wallet["address"], "0xabcdef0000000000000000000000000000000000")
        self.assertEqual(wallet["label"], "Cold")
        updated = self.db.add_wallet(wallet["address"], "Renamed", "")
        self.assertEqual(updated["label"], "Renamed")
        self.assertEqual(len(self.db.list_wallets()), 1)
        self.assertTrue(self.db.remove_wallet(wallet["address"]))
        self.assertFalse(self.db.remove_wallet(wallet["address"]))
        self.assertEqual(self.db.list_wallets(), [])

    def test_airdrops_registry_entries_cannot_be_deleted(self):
        self.db.upsert_airdrop("bundled", {"id": "bundled"}, source="registry")
        self.db.upsert_airdrop("custom", {"id": "custom"}, source="custom")
        self.assertFalse(self.db.delete_airdrop("bundled"))
        self.assertTrue(self.db.delete_airdrop("custom"))
        self.assertIsNone(self.db.get_airdrop("custom"))
        self.assertIsNotNone(self.db.get_airdrop("bundled"))

    def test_airdrop_enable_toggle(self):
        self.db.upsert_airdrop("one", {"id": "one"})
        self.assertEqual(len(self.db.list_airdrops(enabled_only=True)), 1)
        self.assertTrue(self.db.set_airdrop_enabled("one", False))
        self.assertEqual(self.db.list_airdrops(enabled_only=True), [])
        self.assertEqual(len(self.db.list_airdrops()), 1)

    def test_job_lifecycle_serialises_result(self):
        self.db.create_job("job1", "scan")
        self.assertEqual(self.db.get_job("job1")["status"], "queued")
        self.db.update_job("job1", status="done", progress=1.0, result={"snapshot_id": 3, "alerts": 2})
        job = self.db.get_job("job1")
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"]["snapshot_id"], 3)
        self.assertEqual(len(self.db.recent_jobs()), 1)

    def test_snapshot_persistence_and_previous_claims(self):
        first = self.db.save_snapshot(
            chains=["ethereum"],
            wallets=[report_payload()["wallets"][0]],
            totals=report_payload()["totals"],
            report=report_payload(),
            trigger="test",
        )
        self.assertEqual(len(self.db.holdings(first)), 1)
        claims = self.db.claims(first)
        self.assertEqual(claims[0]["status"], "unclaimed")
        self.assertEqual(claims[0]["proof_verified"], 1)
        self.assertEqual(self.db.claim(first, "demo-drop", "0x" + "11" * 20)["net_usd"], 23.0)

        second = self.db.save_snapshot(
            chains=["ethereum"],
            wallets=[report_payload()["wallets"][0]],
            totals=report_payload()["totals"],
            report=report_payload(status="claimed"),
            trigger="test",
        )
        self.assertEqual(self.db.previous_claims(second), {("demo-drop", "0x" + "11" * 20): "unclaimed"})
        self.assertEqual(self.db.claims(second, status="claimed")[0]["status"], "claimed")
        self.assertEqual(self.db.claims(second, status="unclaimed"), [])
        self.assertEqual(self.db.claims(second, min_net=1000), [])
        self.assertEqual(self.db.latest_snapshot()["id"], second)
        self.assertEqual(len(self.db.list_snapshots()), 2)

    def test_alerts_dedupe_and_stay_acknowledged(self):
        kwargs = dict(kind="deadline", severity="critical", title="Due", body="Soon", wallet="0xabc", drop_id="d")
        self.db.add_alert(**kwargs)
        self.db.add_alert(**kwargs)
        self.assertEqual(len(self.db.list_alerts()), 1)
        alert_id = self.db.list_alerts()[0]["id"]
        self.assertTrue(self.db.ack_alert(alert_id))
        self.assertEqual(self.db.list_alerts(), [])
        self.assertEqual(len(self.db.list_alerts(include_ack=True)), 1)
        self.db.add_alert(**kwargs)
        self.assertEqual(self.db.list_alerts(), [])
        self.assertEqual(len(self.db.list_alerts(include_ack=True)), 1)
        self.assertEqual(self.db.ack_all_alerts(), 0)

        # Escalation still reaches the user: same signal, meaningfully different text.
        self.db.add_alert(**{**kwargs, "title": "Due tomorrow", "severity": "critical"})
        open_alerts = self.db.list_alerts()
        self.assertEqual(len(open_alerts), 1)
        self.assertEqual(open_alerts[0]["title"], "Due tomorrow")

    def test_executions_and_audit(self):
        self.db.add_execution(
            wallet="0x" + "11" * 20,
            drop_id="d",
            chain="ethereum",
            tx_hash="0x" + "ab" * 32,
            status="prepared",
        )
        self.assertEqual(self.db.list_executions()[0]["status"], "prepared")
        self.db.log("api", "test.action", "detail")
        entry = self.db.list_audit()[0]
        self.assertEqual(entry["action"], "test.action")
        self.assertEqual(entry["detail"], "detail")

    def test_stats_on_empty_database(self):
        stats = self.db.stats()
        self.assertEqual(stats["wallets"], 0)
        self.assertIsNone(stats["snapshot"])
        self.assertEqual(stats["unacked_alerts"], 0)

    def test_stats_after_snapshot(self):
        self.db.add_wallet("0x" + "11" * 20)
        self.db.save_snapshot(
            chains=["ethereum"],
            wallets=["0x" + "11" * 20],
            totals=report_payload()["totals"],
            report=report_payload(),
        )
        stats = self.db.stats()
        self.assertEqual(stats["wallets"], 1)
        self.assertEqual(stats["snapshot"]["unclaimed_count"], 1)
        self.assertEqual(stats["snapshot"]["claim_gas_usd"], 2.0)

    def test_history_rollups_and_snapshot_filters(self):
        wallet = "0x" + "11" * 20
        other = "0x" + "22" * 20
        self.db.add_wallet(wallet)
        self.db.add_wallet(other)

        first = self.db.save_snapshot(
            chains=["ethereum"],
            wallets=[wallet],
            totals=report_payload(idle=100.0, unclaimed=25.0)["totals"],
            report=report_payload(idle=100.0, unclaimed=25.0),
        )
        second = self.db.save_snapshot(
            chains=["ethereum"],
            wallets=[wallet],
            totals=report_payload(idle=150.0, unclaimed=40.0, status="claimed")["totals"],
            report=report_payload(idle=150.0, unclaimed=40.0, status="claimed"),
        )

        self.assertEqual(self.db.previous_snapshot(second)["id"], first)
        self.assertIsNone(self.db.previous_snapshot(first))

        history = self.db.drop_history("demo-drop")
        self.assertEqual([row["id"] for row in history], [first, second])
        self.assertEqual(history[-1]["claimed"], 1)
        self.assertEqual(history[-1]["unclaimed"], 0)
        self.assertAlmostEqual(history[0]["gross_usd"], 25.0)

        wallet_history = self.db.wallet_history(wallet)
        self.assertEqual([row["id"] for row in wallet_history], [first, second])
        self.assertAlmostEqual(wallet_history[-1]["idle_usd"], 2000.0)
        self.assertEqual(wallet_history[-1]["unclaimed_usd"], 0.0)

        self.assertEqual(len(self.db.holdings(second)), 1)
        self.assertEqual(self.db.holdings(second, wallet=other), [])
        self.assertEqual(len(self.db.claims(second, drop_id="demo-drop")), 1)
        self.assertEqual(self.db.claims(second, drop_id="other-drop"), [])
        self.assertEqual(len(self.db.claims(second, wallet=wallet)), 1)
        self.assertEqual(self.db.claims(second, wallet=other), [])


if __name__ == "__main__":
    unittest.main()
