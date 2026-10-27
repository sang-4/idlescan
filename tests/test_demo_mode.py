"""Demo mode must not leak into the real database, and must be reversible."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from idlescan.cli import main
from idlescan.config import Settings
from idlescan.db import Database
from idlescan.selftest import WALLET_CLAIMED, WALLET_EMPTY, WALLET_UNCLAIMED
from idlescan.service import PlatformService

REAL_WALLET = "0x" + "ab" * 20


class DemoPurgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "idlescan.db"

    def _seeded_service(self) -> PlatformService:
        service = PlatformService(Settings(db_path=self.db_path, demo=True))
        service.seed_demo()
        return service

    def test_seed_demo_creates_synthetic_rows(self):
        service = self._seeded_service()
        addresses = {wallet["address"] for wallet in service.list_wallets()}
        self.assertEqual(
            addresses,
            {WALLET_CLAIMED, WALLET_EMPTY, WALLET_UNCLAIMED},
        )
        self.assertIn("demo-merkle", {drop["id"] for drop in service.airdrops()})
        self.assertTrue(service.db.list_snapshots())

    def test_purge_removes_only_demo_rows(self):
        seeded = self._seeded_service()
        seeded.add_wallet(REAL_WALLET, "real wallet", "mine")

        counts = PlatformService(Settings(db_path=self.db_path, demo=False)).purge_demo_data()

        self.assertEqual(counts["wallets"], 3)
        self.assertEqual(counts["airdrops"], 1)
        self.assertGreaterEqual(counts["snapshots"], 1)

        self.assertEqual([wallet["address"] for wallet in seeded.list_wallets()], [REAL_WALLET])
        self.assertEqual([drop["id"] for drop in seeded.airdrops()], ["uniswap-uni"])
        self.assertEqual(seeded.db.list_snapshots(), [])

        with Database(self.db_path).connect() as connection:
            for table in ("holdings", "claims", "alerts"):
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.assertEqual(count, 0, table)

    def test_purge_is_idempotent(self):
        self._seeded_service()
        service = PlatformService(Settings(db_path=self.db_path, demo=False))
        self.assertTrue(any(service.purge_demo_data().values()))
        self.assertFalse(any(service.purge_demo_data().values()))

    def test_purge_removes_scans_of_demo_wallets(self):
        seeded = self._seeded_service()
        # A scan of the synthetic wallets, triggered as a normal UI scan.
        seeded.run_scan_sync(trigger="ui")
        self.assertEqual(len(seeded.db.list_snapshots()), 2)

        PlatformService(Settings(db_path=self.db_path, demo=False)).purge_demo_data()
        self.assertEqual(seeded.db.list_snapshots(), [])


class DemoServeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.env = self.root / "test.env"
        self.env.write_text(f"IDLESCAN_DB={self.root / 'real.db'}\nIDLESCAN_DEMO=false\n")

    def _run_serve(self, *extra: str):
        with patch("idlescan.api.run", return_value=0) as run, patch("idlescan.log.configure_logging"):
            code = main(["serve", "--env-file", str(self.env), "--port", "0", *extra])
        return code, run

    def test_demo_uses_an_isolated_database(self):
        code, run = self._run_serve("--demo")
        self.assertEqual(code, 0)
        settings = run.call_args.args[0]
        self.assertTrue(settings.demo)
        self.assertEqual(settings.db_path.name, "idlescan-demo.db")
        self.assertEqual(settings.db_path.parent, self.root)

    def test_explicit_database_wins_in_demo_mode(self):
        custom = self.root / "custom-demo.db"
        _, run = self._run_serve("--demo", "--db", str(custom))
        self.assertEqual(run.call_args.args[0].db_path, custom)

    def test_non_demo_keep_the_real_database(self):
        _, run = self._run_serve()
        settings = run.call_args.args[0]
        self.assertFalse(settings.demo)
        self.assertEqual(settings.db_path, self.root / "real.db")


if __name__ == "__main__":
    unittest.main()
