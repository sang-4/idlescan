"""The two non-dataset eligibility modes, plus deadlines.

These paths talk to contracts (allocation views) and to third-party APIs
(per-address claim endpoints), so they are tested against purpose-built mocks
rather than left to chance.
"""

import unittest
import urllib.error

from idlescan.abi import encode, selector
from idlescan.cache import Cache
from idlescan.chains import Chain, ChainConfig, NativeAsset
from idlescan.merkle import build_proof
from idlescan.prices import PriceOracle
from idlescan.registry import Airdrop, Eligibility
from idlescan.scanner import (
    CLAIMED,
    ERROR,
    NO_ALLOCATION,
    UNCLAIMED,
    UNKNOWN,
    Scanner,
)
from idlescan.selftest import WALLET_CLAIMED, WALLET_EMPTY, WALLET_UNCLAIMED, MockRpc, build_world

ALLOC_DISTRIBUTOR = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
SEL_ALLOCATION = selector("allocation(address)")
SEL_CLAIMED = selector("claimed(address)")

ALLOCATIONS = {WALLET_UNCLAIMED: 70 * 10**18, WALLET_CLAIMED: 12 * 10**18}
CLAIMED_ADDRESSES = {WALLET_CLAIMED}


class AllocationRpc(MockRpc):
    def _eth_getCode(self, address: str, block: str = "latest") -> str:
        if address.lower() == ALLOC_DISTRIBUTOR:
            return "0x6080604052"
        return super()._eth_getCode(address, block)

    def _eth_call(self, tx: dict, block: str = "latest") -> str:
        to = (tx.get("to") or "").lower()
        raw = bytes.fromhex((tx.get("data") or "").removeprefix("0x"))
        sel, args = raw[:4], raw[4:]
        if to == ALLOC_DISTRIBUTOR:
            who = "0x" + args[12:32].hex()
            if sel == SEL_ALLOCATION:
                return "0x" + encode(["uint256"], [ALLOCATIONS.get(who.lower(), 0)]).hex()
            if sel == SEL_CLAIMED:
                return "0x" + encode(["bool"], [who.lower() in CLAIMED_ADDRESSES]).hex()
        return super()._eth_call(tx, block)


class ModesBase(unittest.TestCase):
    chain = Chain(
        key="ethereum",
        chain_id=1,
        name="Mock Mainnet",
        native=NativeAsset(symbol="ETH", decimals=18, coingecko="ethereum"),
        rpcs=("mock://",),
    )

    def scanner(self, drop, *, rpc=None, http_fetch=None):
        return Scanner(
            ChainConfig(chains={"ethereum": self.chain}),
            {},
            [drop],
            cache=Cache(enabled=False),
            prices=PriceOracle(enabled=False),
            rpc_factory=lambda chain: rpc or MockRpc(build_world()),
            http_fetch=http_fetch,
        )


class AllocationViewTests(ModesBase):
    def drop(self, deadline=None):
        return Airdrop(
            id="alloc",
            name="Alloc Token (ALC)",
            chain="ethereum",
            distributor=ALLOC_DISTRIBUTOR,
            token=None,
            eligibility=Eligibility(type="allocation_view", method="allocation(address)", decimals=18),
            claimed_method="claimed(address)",
            root_method=None,
            claim_deadline=deadline,
        )

    def test_unclaimed_claimed_and_zero(self):
        scanner = self.scanner(self.drop(), rpc=AllocationRpc(build_world()))
        report = scanner.scan(["ethereum"], [WALLET_UNCLAIMED, WALLET_CLAIMED, WALLET_EMPTY])
        by_wallet = {c.wallet: c for c in report.claims}
        self.assertEqual(by_wallet[WALLET_UNCLAIMED].status, UNCLAIMED)
        self.assertEqual(by_wallet[WALLET_UNCLAIMED].amount_raw, 70 * 10**18)
        self.assertEqual(by_wallet[WALLET_CLAIMED].status, CLAIMED)
        self.assertEqual(by_wallet[WALLET_EMPTY].status, NO_ALLOCATION)

    def test_expired_deadline_marks_claimable_false(self):
        scanner = self.scanner(self.drop(deadline="2020-01-01T00:00:00Z"), rpc=AllocationRpc(build_world()))
        report = scanner.scan(["ethereum"], [WALLET_UNCLAIMED])
        row = report.claims[0]
        self.assertTrue(row.expired)
        self.assertFalse(row.claimable)
        self.assertEqual(report.claim_value, None)

    def test_failed_claimed_call_is_unknown_not_unclaimed(self):
        class Broken(AllocationRpc):
            def _eth_call(self, tx, block="latest"):
                to = (tx.get("to") or "").lower()
                raw = bytes.fromhex((tx.get("data") or "").removeprefix("0x"))
                if to == ALLOC_DISTRIBUTOR and raw[:4] == SEL_CLAIMED:
                    from idlescan.rpc import RpcError
                    raise RpcError("node timeout")
                return super()._eth_call(tx, block="latest")

        scanner = self.scanner(self.drop(), rpc=Broken(build_world()))
        report = scanner.scan(["ethereum"], [WALLET_UNCLAIMED])
        row = report.claims[0]
        self.assertEqual(row.status, UNKNOWN)
        self.assertIn("claim state", row.note or "")


class DatasetUrlTests(ModesBase):
    def drop(self):
        return Airdrop(
            id="api",
            name="API Drop (API)",
            chain="ethereum",
            distributor="0xdddddddddddddddddddddddddddddddddddddddd",
            token=None,
            eligibility=Eligibility(type="dataset_url", url="https://claims.example/{address}"),
            claimed_method="isClaimed(uint256)",
        )

    def world_payload(self, world, wallet):
        index, amount = next(
            (i, amt) for i, (account, amt) in enumerate(world.claims) if account == wallet
        )
        proof = build_proof(index, wallet, amount, world.claims, encoding="packed")
        return {
            "merkleRoot": "0x" + world.root.hex(),
            "claims": {
                wallet: {
                    "index": index,
                    "amount": hex(amount),
                    "proof": ["0x" + p.hex() for p in proof],
                }
            },
        }

    def test_valid_api_payload_is_trusted_only_after_proof_check(self):
        world = build_world()
        payload = self.world_payload(world, WALLET_UNCLAIMED)

        def fetch(url):
            self.assertIn(WALLET_UNCLAIMED, url)
            return payload

        scanner = self.scanner(self.drop(), http_fetch=fetch)
        report = scanner.scan(["ethereum"], [WALLET_UNCLAIMED])
        row = report.claims[0]
        self.assertEqual(row.status, UNCLAIMED)
        self.assertIs(row.proof_verified, True)

    def test_404_means_absent_not_error(self):
        def fetch(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        scanner = self.scanner(self.drop(), http_fetch=fetch)
        report = scanner.scan(["ethereum"], [WALLET_EMPTY])
        self.assertEqual(report.claims[0].status, NO_ALLOCATION)

    def test_tampered_api_proof_is_rejected(self):
        world = build_world()
        payload = self.world_payload(world, WALLET_UNCLAIMED)
        payload["claims"][WALLET_UNCLAIMED]["proof"][0] = "0x" + "00" * 32

        scanner = self.scanner(self.drop(), http_fetch=lambda url: payload)
        report = scanner.scan(["ethereum"], [WALLET_UNCLAIMED])
        row = report.claims[0]
        self.assertEqual(row.status, ERROR)
        self.assertIs(row.proof_verified, False)

    def test_api_failure_is_error(self):
        def fetch(url):
            raise urllib.error.URLError("dns failure")

        scanner = self.scanner(self.drop(), http_fetch=fetch)
        report = scanner.scan(["ethereum"], [WALLET_UNCLAIMED])
        self.assertEqual(report.claims[0].status, ERROR)


if __name__ == "__main__":
    unittest.main()
