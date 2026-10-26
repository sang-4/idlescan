"""Scanner behaviour against a mock chain.

These are the tests that matter for correctness of the claim logic: claimed vs
unclaimed, missing allocation, dataset/contract mismatch, tampered proofs, leaf
encoding auto-detection, and chain-id safety.
"""

import unittest

from idlescan.cache import Cache
from idlescan.merkle import Claim, ClaimDataset, build_proof, compute_root, leaf_hash
from idlescan.prices import PriceOracle
from idlescan.scanner import (
    CLAIMED,
    ERROR,
    NO_ALLOCATION,
    NO_SOURCE,
    UNCLAIMED,
    Scanner,
)
from idlescan.selftest import (
    WALLET_CLAIMED,
    WALLET_EMPTY,
    WALLET_UNCLAIMED,
    MockRpc,
    build_config,
    build_world,
)


def make_dataset(world, *, encoding="packed", root=None, tamper=None):
    claims = {}
    for index, (account, amount) in enumerate(world.claims):
        proof = build_proof(index, account, amount, world.claims, encoding=encoding)
        if tamper and account == tamper:
            proof = list(proof)
            if proof:
                proof[0] = bytes(32)
        claims[account.lower()] = Claim(index=index, amount=amount, proof=tuple(proof))
    return ClaimDataset(
        claims=claims,
        root=root if root is not None else world.root,
        source="test",
        encoding=encoding,
    )


def run(world=None, dataset=None, *, chain_id=1, wallets=None):
    world = world or build_world()
    chains, tokens, drops = build_config()
    rpc = MockRpc(world, chain_id=chain_id)
    scanner = Scanner(
        chains,
        tokens,
        drops,
        cache=Cache(enabled=False),
        prices=PriceOracle(enabled=False),
        rpc_factory=lambda chain: rpc,
    )
    if dataset is not None:
        scanner._datasets["demo-merkle"] = dataset
    report = scanner.scan(["ethereum"], wallets or world.wallets)
    return report, scanner


class ClaimStatusTests(unittest.TestCase):
    def test_happy_path(self):
        world = build_world()
        report, _ = run(world, make_dataset(world))
        by_wallet = {c.wallet: c for c in report.claims}
        self.assertEqual(by_wallet[WALLET_UNCLAIMED].status, UNCLAIMED)
        self.assertEqual(by_wallet[WALLET_CLAIMED].status, CLAIMED)
        self.assertEqual(by_wallet[WALLET_EMPTY].status, NO_ALLOCATION)
        self.assertIs(by_wallet[WALLET_UNCLAIMED].proof_verified, True)
        self.assertGreater(by_wallet[WALLET_UNCLAIMED].gas_native or 0, 0)
        # ready-to-send calldata for a manual/direct claim
        calldata = by_wallet[WALLET_UNCLAIMED].calldata
        self.assertIsNotNone(calldata)
        from idlescan.abi import decode, selector
        self.assertTrue(calldata.startswith("0x" + selector("claim(uint256,address,uint256,bytes32[])").hex()))
        types = ["uint256", "address", "uint256", "bytes32[]"]
        index, account, amount, proof = decode(types, calldata[10:])
        self.assertEqual(index, 0)
        self.assertEqual(account, WALLET_UNCLAIMED)
        self.assertEqual(amount, 300 * 10**18)
        self.assertEqual(len(proof), 2)
        self.assertIsNone(by_wallet[WALLET_UNCLAIMED].net_usd)  # no prices in tests

    def test_balances_found(self):
        world = build_world()
        report, _ = run(world, make_dataset(world))
        native = [h for h in report.holdings if h.is_native and h.wallet == WALLET_UNCLAIMED]
        self.assertEqual(native[0].raw, 420_000_000_000_000_000)
        token = [h for h in report.holdings if not h.is_native and h.wallet == WALLET_UNCLAIMED]
        self.assertEqual(token[0].symbol, "USDC")
        self.assertEqual(str(token[0].amount), "250")

    def test_missing_dataset_reports_gap_not_zero(self):
        world = build_world()
        report, _ = run(world, None)
        self.assertTrue(report.claims)
        self.assertTrue(all(c.status == NO_SOURCE for c in report.claims), [c.status for c in report.claims])

    def test_root_mismatch_is_an_error(self):
        world = build_world()
        dataset = make_dataset(world, root=b"\x11" * 32)
        report, _ = run(world, dataset)
        self.assertTrue(all(c.status == ERROR for c in report.claims))
        self.assertTrue(any("root" in (c.note or "") for c in report.claims))

    def test_tampered_proof_is_rejected(self):
        world = build_world()
        dataset = make_dataset(world, tamper=WALLET_UNCLAIMED)
        report, _ = run(world, dataset)
        row = next(c for c in report.claims if c.wallet == WALLET_UNCLAIMED)
        self.assertEqual(row.status, ERROR)
        self.assertIs(row.proof_verified, False)
        self.assertIn("proof", (row.note or ""))

    def test_abi_leaf_encoding_autodetected(self):
        world = build_world()
        # Rebuild the world's root with abi.encode leaves and matching proofs.
        statements = [
            (account, amount) for account, amount in world.claims
        ]
        leaves = [
            leaf_hash(i, account, amount, encoding="abi")
            for i, (account, amount) in enumerate(statements)
        ]
        world.root = compute_root(leaves)
        dataset = make_dataset(world, encoding="abi")
        report, scanner = run(world, dataset)
        row = next(c for c in report.claims if c.wallet == WALLET_UNCLAIMED)
        self.assertEqual(row.status, UNCLAIMED)
        self.assertIs(row.proof_verified, True)
        self.assertEqual(scanner._leaf_modes["demo-merkle"], ("abi", False))

    def test_claimed_flips_to_unclaimed_when_state_changes(self):
        world = build_world()
        world.claimed_indexes = set()
        report, _ = run(world, make_dataset(world))
        by_wallet = {c.wallet: c for c in report.claims}
        self.assertEqual(by_wallet[WALLET_CLAIMED].status, UNCLAIMED)

    def test_wrong_chain_is_skipped(self):
        world = build_world()
        report, _ = run(world, make_dataset(world), chain_id=137)
        self.assertEqual(report.holdings, [])
        self.assertEqual(report.claims, [])
        self.assertTrue(any("chain id" in w for w in report.warnings), report.warnings)

    def test_no_gas_estimation_option(self):
        world = build_world()
        chains, tokens, drops = build_config()
        scanner = Scanner(
            chains,
            tokens,
            drops,
            cache=Cache(enabled=False),
            prices=PriceOracle(enabled=False),
            rpc_factory=lambda chain: MockRpc(world),
            estimate_gas=False,
        )
        scanner._datasets["demo-merkle"] = make_dataset(world)
        report = scanner.scan(["ethereum"], world.wallets)
        row = next(c for c in report.claims if c.wallet == WALLET_UNCLAIMED)
        self.assertIsNone(row.gas_native)


if __name__ == "__main__":
    unittest.main()
