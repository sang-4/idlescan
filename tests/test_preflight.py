"""Claim preflight: stale state, tampering, reverts and expiry are refused."""

import tempfile
import unittest
from pathlib import Path

from idlescan.abi import calldata, selector
from idlescan.config import Settings
from idlescan.rpc import RpcError
from idlescan.selftest import (
    DISTRIBUTOR,
    WALLET_UNCLAIMED,
    MockRpc,
    build_world,
)
from idlescan.service import Conflict, PlatformService, ServiceError

SEL_CLAIM = "claim(uint256,address,uint256,bytes32[])"


class PreflightRpc(MockRpc):
    def __init__(self, world):
        super().__init__(world)
        self.already_claimed = False
        self.no_code = False
        self.revert = False
        self.root_override = None
        self.block = 19_000_100

    def _eth_getCode(self, address, block="latest"):
        if self.no_code and address.lower() == DISTRIBUTOR:
            return "0x"
        return super()._eth_getCode(address, block)

    def _eth_blockNumber(self):
        return hex(self.block)

    def _eth_call(self, tx, block="latest"):
        to = tx.get("to", "").lower()
        raw = bytes.fromhex((tx.get("data") or "")[2:])
        sel = raw[:4]
        if to == DISTRIBUTOR and sel.hex() == selector("merkleRoot()").hex():
            if self.root_override is not None:
                return "0x" + self.root_override.hex()
        if to == DISTRIBUTOR and sel.hex() == selector("isClaimed(uint256)").hex():
            return "0x" + (b"\x00" * 31 + bytes([1 if self.already_claimed else 0])).hex()
        if to == DISTRIBUTOR and sel.hex() == selector(SEL_CLAIM).hex():
            if self.revert:
                raise RpcError("execution reverted: Claim window closed")
            return "0x" + (b"\x00" * 31 + b"\x01").hex()
        return super()._eth_call(tx, block)


class PreflightTests(unittest.TestCase):
    read_only = False

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            read_only=False,
            db_path=Path(self._tmp.name) / "preflight.db",
        )
        self.world = build_world()
        self.rpc = PreflightRpc(self.world)
        self.service = PlatformService(self.settings, rpc_factory=lambda chain: self.rpc)
        self.service.seed_demo()

    def tearDown(self):
        self._tmp.cleanup()

    def prepare(self):
        return self.service.prepare_claim("demo-merkle", WALLET_UNCLAIMED)

    def test_successful_preflight_returns_evidence_and_zero_value(self):
        result = self.prepare()
        tx = result["transaction"]
        self.assertEqual(tx["value"], "0x0")
        self.assertEqual(tx["to"], DISTRIBUTOR)
        self.assertIn("maxFeePerGas", tx)
        self.assertEqual(tx["maxPriorityFeePerGas"], hex(1_000_000_000))
        self.assertTrue(tx["gasLimit"])
        preflight = result["preflight"]
        self.assertEqual(preflight["block_number"], 19_000_100)
        self.assertEqual(preflight["call"]["index"], 0)
        self.assertEqual(preflight["call"]["account"], WALLET_UNCLAIMED)
        self.assertEqual(len(preflight["call"]["proof"]), 2)
        self.assertEqual(preflight["call"]["values"][0]["type"], "uint256")
        self.assertIn("distributor", preflight)
        self.assertIn("explorer_url", preflight)
        self.assertTrue(result["what_you_are_about_to_sign"])
        self.assertTrue(
            any("never asks you to send ETH" in line for line in result["what_you_are_about_to_sign"])
        )

    def test_already_claimed_since_snapshot_is_refused(self):
        self.rpc.already_claimed = True
        with self.assertRaises(Conflict) as ctx:
            self.prepare()
        self.assertIn("already claimed", str(ctx.exception))
        executions = self.service.db.list_executions()
        self.assertEqual(executions[0]["status"], "preflight_failed")

    def test_root_change_is_refused(self):
        self.rpc.root_override = b"\x99" * 32
        with self.assertRaises(Conflict) as ctx:
            self.prepare()
        self.assertIn("merkle root changed", str(ctx.exception))

    def test_missing_code_is_refused(self):
        self.rpc.no_code = True
        with self.assertRaises(Conflict) as ctx:
            self.prepare()
        self.assertIn("no contract code", str(ctx.exception))

    def test_revert_reason_is_surfaced(self):
        self.rpc.revert = True
        with self.assertRaises(Conflict) as ctx:
            self.prepare()
        self.assertIn("Claim window closed", str(ctx.exception))
        self.assertIn("simulation failed", str(ctx.exception))

    def test_calldata_amount_mismatch_is_refused(self):
        with self.service.db.connect() as connection:
            connection.execute(
                "UPDATE claims SET calldata = ? WHERE wallet = ? AND drop_id = ?",
                (
                    calldata(
                        SEL_CLAIM,
                        ["uint256", "address", "uint256", "bytes32[]"],
                        [0, WALLET_UNCLAIMED, 1, ["0x" + "00" * 32, "0x" + "00" * 32]],
                    ),
                    WALLET_UNCLAIMED,
                    "demo-merkle",
                ),
            )
        with self.assertRaises(ServiceError) as ctx:
            self.prepare()
        self.assertIn("does not match verified position", str(ctx.exception))

    def test_expired_deadline_is_rechecked_at_prepare_time(self):
        payload = self.service.db.get_airdrop("demo-merkle")["payload"]
        payload["claim_deadline"] = "2020-01-01T00:00:00+00:00"
        self.service.db.upsert_airdrop("demo-merkle", payload, source="demo")
        with self.assertRaises(Conflict) as ctx:
            self.prepare()
        self.assertIn("expired", str(ctx.exception))

    def test_payable_claim_requires_explicit_registry_opt_in(self):
        payload = self.service.db.get_airdrop("demo-merkle")["payload"]
        payload["claim_payable"] = True
        payload["allow_payable"] = False
        self.service.db.upsert_airdrop("demo-merkle", payload, source="demo")
        with self.assertRaises(ServiceError) as ctx:
            self.prepare()
        self.assertIn("payable", str(ctx.exception))

    def test_failure_is_recorded_in_audit(self):
        self.rpc.already_claimed = True
        with self.assertRaises(Conflict):
            self.prepare()
        actions = [row["action"] for row in self.service.db.list_audit()]
        self.assertIn("claim.preflight_failed", actions)


if __name__ == "__main__":
    unittest.main()
