"""Gas model: EIP-1559, legacy, L1 fees, failure and ranking behaviour."""

import tempfile
import unittest
from pathlib import Path

from idlescan.chains import Chain, NativeAsset
from idlescan.db import Database
from idlescan.gas import (
    FULL,
    PARTIAL,
    UNAVAILABLE,
    buffered_wei,
    estimate_claim,
    quote_fees,
)
from idlescan.rpc import RpcError
from idlescan.scanner import UNCLAIMED, ClaimCheck
from idlescan.selftest import DISTRIBUTOR, MockRpc, build_world

DATA = "0x" + "ab" * 4
WALLET = "0x" + "11" * 20


def chain(**overrides):
    values = dict(
        key="test",
        chain_id=1,
        name="Test",
        native=NativeAsset(symbol="ETH", decimals=18),
        rpcs=("mock://test",),
    )
    values.update(overrides)
    return Chain(**values)


class BaseRpc(MockRpc):
    def __init__(self, world=None):
        super().__init__(world or build_world())
        self.overrides = {}

    def request(self, method, *params):
        self.calls += 1
        if method in self.overrides:
            value = self.overrides[method]
            if isinstance(value, Exception):
                raise value
            return value
        handler = getattr(self, f"_{method}", None)
        if handler is None:
            raise RpcError(f"mock does not implement {method}")
        return handler(*params)


class FeeQuoteTests(unittest.TestCase):
    def test_fee_history_sets_base_and_priority(self):
        rpc = BaseRpc()
        quote = quote_fees(rpc, chain())
        self.assertEqual(quote.model, "eip1559")
        self.assertEqual(quote.base_fee, 10_000_000_000)
        self.assertEqual(quote.max_priority_fee_per_gas, 1_000_000_000)
        self.assertEqual(quote.gas_price, 11_000_000_000)
        self.assertEqual(quote.max_fee_per_gas, 21_000_000_000)
        self.assertEqual(quote.source, "eth_feeHistory")
        self.assertEqual(quote.quality, FULL)

    def test_priority_fee_fallback(self):
        rpc = BaseRpc()
        rpc.overrides["eth_feeHistory"] = RpcError("method not found")
        quote = quote_fees(rpc, chain())
        self.assertEqual(quote.source, "eth_maxPriorityFeePerGas")
        self.assertEqual(quote.gas_price, 11_000_000_000)
        self.assertEqual(quote.quality, FULL)

    def test_gas_price_fallback_is_partial(self):
        rpc = BaseRpc()
        rpc.overrides["eth_feeHistory"] = RpcError("method not found")
        rpc.overrides["eth_maxPriorityFeePerGas"] = RpcError("method not found")
        quote = quote_fees(rpc, chain())
        self.assertEqual(quote.source, "eth_gasPrice")
        self.assertEqual(quote.gas_price, 12_000_000_000)
        self.assertEqual(quote.max_fee_per_gas, 24_000_000_000)
        self.assertEqual(quote.quality, PARTIAL)
        self.assertIn("eth_gasPrice", quote.note or "")

    def test_legacy_chain_uses_gas_price_only(self):
        rpc = BaseRpc()
        rpc.overrides["eth_feeHistory"] = RpcError("should not be called")
        quote = quote_fees(rpc, chain(fee_model="legacy"))
        self.assertEqual(quote.model, "legacy")
        self.assertEqual(quote.gas_price, 12_000_000_000)
        self.assertEqual(quote.max_priority_fee_per_gas, 0)
        self.assertEqual(quote.quality, FULL)

    def test_no_quote_raises(self):
        rpc = BaseRpc()
        rpc.overrides["eth_feeHistory"] = RpcError("no")
        rpc.overrides["eth_maxPriorityFeePerGas"] = RpcError("no")
        rpc.overrides["eth_gasPrice"] = RpcError("no")
        with self.assertRaises(RpcError):
            quote_fees(rpc, chain())


class GasEstimateTests(unittest.TestCase):
    def test_estimate_includes_execution_cost_and_headroom(self):
        rpc = BaseRpc()
        estimate = estimate_claim(rpc, chain(), wallet=WALLET, distributor=DISTRIBUTOR, data=DATA)
        self.assertEqual(estimate.gas_units, 180_000)
        self.assertEqual(estimate.gas_limit, 225_000)  # 25% headroom
        self.assertEqual(estimate.l1_fee_wei, 0)
        self.assertEqual(estimate.quality, FULL)
        self.assertEqual(estimate.cost_wei, 180_000 * 11_000_000_000)

    def test_op_stack_l1_fee_is_added_and_labelled_partial(self):
        rpc = BaseRpc()

        def get_l1_fee(tx, block="latest"):
            self.assertTrue(tx["to"].lower().startswith("0x4200"))
            return hex(300_000_000_000_000)

        rpc._eth_call = get_l1_fee
        estimate = estimate_claim(rpc, chain(l1_fee="op_stack"), wallet=WALLET, distributor=DISTRIBUTOR, data=DATA)
        self.assertEqual(estimate.l1_fee_wei, 300_000_000_000_000)
        self.assertEqual(estimate.quality, PARTIAL)
        self.assertIn("GasPriceOracle", estimate.note or "")
        self.assertEqual(
            estimate.cost_wei,
            180_000 * 11_000_000_000 + 300_000_000_000_000,
        )

    def test_unknown_l1_component_is_partial_not_precise(self):
        rpc = BaseRpc()
        estimate = estimate_claim(rpc, chain(l1_fee="unknown"), wallet=WALLET, distributor=DISTRIBUTOR, data=DATA)
        self.assertIsNone(estimate.l1_fee_wei)
        self.assertEqual(estimate.quality, PARTIAL)
        self.assertIn("not modelled", estimate.note or "")

    def test_op_stack_oracle_failure_is_partial(self):
        rpc = BaseRpc()

        def fail(tx, block="latest"):
            raise RpcError("execution reverted")

        rpc._eth_call = fail
        estimate = estimate_claim(rpc, chain(l1_fee="op_stack"), wallet=WALLET, distributor=DISTRIBUTOR, data=DATA)
        self.assertIsNone(estimate.l1_fee_wei)
        self.assertEqual(estimate.quality, PARTIAL)
        self.assertIn("unavailable", estimate.note or "")

    def test_estimation_failure_is_unavailable_and_cost_is_none(self):
        rpc = BaseRpc()
        rpc.overrides["eth_estimateGas"] = RpcError("execution reverted")
        estimate = estimate_claim(rpc, chain(), wallet=WALLET, distributor=DISTRIBUTOR, data=DATA)
        self.assertEqual(estimate.quality, UNAVAILABLE)
        self.assertIsNone(estimate.cost_wei)
        self.assertIsNone(estimate.gas_units)
        self.assertIn("gas estimate failed", estimate.note or "")

    def test_fee_failure_is_also_unavailable(self):
        rpc = BaseRpc()
        rpc.overrides["eth_feeHistory"] = RpcError("no")
        rpc.overrides["eth_maxPriorityFeePerGas"] = RpcError("no")
        rpc.overrides["eth_gasPrice"] = RpcError("no")
        estimate = estimate_claim(rpc, chain(), wallet=WALLET, distributor=DISTRIBUTOR, data=DATA)
        self.assertEqual(estimate.quality, UNAVAILABLE)
        self.assertIsNone(estimate.cost_wei)


class BufferAndRankingTests(unittest.TestCase):
    def test_buffer_rounds_up(self):
        self.assertEqual(buffered_wei(100, 20), 120)
        self.assertEqual(buffered_wei(101, 20), 122)  # 121.2 -> 122
        self.assertEqual(buffered_wei(100, 0), 100)
        self.assertIsNone(buffered_wei(None, 20))

    def test_net_uses_buffered_cost_and_keeps_raw_separate(self):
        claim = ClaimCheck(
            drop_id="d",
            name="D",
            chain="ethereum",
            chain_id=1,
            wallet=WALLET,
            distributor=DISTRIBUTOR,
            status=UNCLAIMED,
            amount_raw=10**18,
            usd_price=100.0,
            gas_native=0.01,
            gas_usd=10.0,
            gas_cost_native_buffered=0.012,
            gas_cost_usd_buffered=12.0,
            gas_estimate_quality=FULL,
        )
        self.assertEqual(claim.net_usd, 88.0)
        self.assertEqual(claim.net_usd_raw, 90.0)
        self.assertEqual(claim.net_group, "profitable")

    def test_unavailable_gas_leaves_net_unknown_not_zero(self):
        claim = ClaimCheck(
            drop_id="d",
            name="D",
            chain="ethereum",
            chain_id=1,
            wallet=WALLET,
            distributor=DISTRIBUTOR,
            status=UNCLAIMED,
            amount_raw=10**18,
            usd_price=100.0,
            gas_estimate_quality=UNAVAILABLE,
        )
        self.assertIsNone(claim.net_usd)
        self.assertIsNone(claim.net_usd_raw)
        self.assertEqual(claim.net_group, "unknown")

    def test_unknown_net_claims_sort_after_known_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "rank.db").init()
            snapshot = db.save_snapshot(
                chains=["ethereum"],
                wallets=[WALLET],
                totals={},
                report={
                    "claims": [
                        {
                            "drop_id": "unknown",
                            "name": "Unknown gas",
                            "chain": "ethereum",
                            "wallet": WALLET,
                            "distributor": DISTRIBUTOR,
                            "status": UNCLAIMED,
                            "net_usd": None,
                            "gas_estimate_quality": UNAVAILABLE,
                        },
                        {
                            "drop_id": "known",
                            "name": "Known gas",
                            "chain": "ethereum",
                            "wallet": WALLET,
                            "distributor": DISTRIBUTOR,
                            "status": UNCLAIMED,
                            "net_usd": 5.0,
                            "gas_estimate_quality": FULL,
                        },
                    ]
                },
            )
            rows = db.claims(snapshot)
        self.assertEqual([row["drop_id"] for row in rows], ["known", "unknown"])


if __name__ == "__main__":
    unittest.main()
