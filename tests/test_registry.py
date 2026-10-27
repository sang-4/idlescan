"""Registry parsing and validation."""

import unittest

from idlescan.chains import ConfigError, load_chains
from idlescan.registry import load_registry, parse_registry

CHAIN_CONFIG = load_chains()

BASE = {
    "id": "example",
    "name": "Example Drop",
    "chain": "ethereum",
    "distributor": "0x090D4613473dEE047c3f2706764f49E0821D256e",
    "token": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
    "token_decimals": 18,
}


def registry_with(**overrides):
    entry = dict(BASE)
    entry.update(overrides)
    return {"version": 1, "airdrops": [entry]}


class RegistryTests(unittest.TestCase):
    def test_bundled_registry_loads(self):
        drops = load_registry(None, CHAIN_CONFIG)
        self.assertGreaterEqual(len(drops), 1)
        uniswap = next(d for d in drops if d.id == "uniswap-uni")
        self.assertEqual(uniswap.chain, "ethereum")
        self.assertEqual(uniswap.distributor, "0x090d4613473dee047c3f2706764f49e0821d256e")
        self.assertIsNotNone(uniswap.eligibility)
        self.assertEqual(uniswap.eligibility.type, "merkle_dataset")
        # relative dataset path is resolved next to the registry file
        self.assertTrue(uniswap.eligibility.dataset.endswith("datasets/uniswap-uni.claims.json"))

    def test_merkle_entry_defaults(self):
        drops = parse_registry(
            registry_with(eligibility={"type": "merkle_dataset", "dataset": "x.json"}),
            CHAIN_CONFIG,
        )
        drop = drops[0]
        self.assertEqual(drop.claimed_method, "isClaimed(uint256)")
        self.assertEqual(drop.claimed_arg, "index")
        self.assertEqual(drop.eligibility.leaf_encoding, "auto")

    def test_allocation_view_defaults_to_claimed_address(self):
        drops = parse_registry(
            registry_with(eligibility={"type": "allocation_view", "method": "allocation(address)", "decimals": 6}),
            CHAIN_CONFIG,
        )
        self.assertEqual(drops[0].claimed_method, "claimed(address)")
        self.assertEqual(drops[0].claimed_arg, "address")
        self.assertEqual(drops[0].eligibility.decimals, 6)

    def test_unknown_chain_rejected(self):
        with self.assertRaises(ConfigError):
            parse_registry(registry_with(chain="atlantis"), CHAIN_CONFIG)

    def test_bad_address_rejected(self):
        with self.assertRaises(ConfigError):
            parse_registry(registry_with(distributor="0x1234"), CHAIN_CONFIG)

    def test_duplicate_id_rejected(self):
        payload = {"airdrops": [dict(BASE), dict(BASE)]}
        with self.assertRaises(ConfigError):
            parse_registry(payload, CHAIN_CONFIG)

    def test_dataset_url_needs_placeholder(self):
        with self.assertRaises(ConfigError):
            parse_registry(
                registry_with(eligibility={"type": "dataset_url", "url": "https://example.com/claims"}),
                CHAIN_CONFIG,
            )

    def test_unknown_eligibility_type_rejected(self):
        with self.assertRaises(ConfigError):
            parse_registry(registry_with(eligibility={"type": "magic"}), CHAIN_CONFIG)

    def test_disabled_entry_is_kept_but_flagged(self):
        drops = parse_registry(registry_with(enabled=False), CHAIN_CONFIG)
        self.assertFalse(drops[0].enabled)

    def test_no_root_method_disables_root_check(self):
        drops = parse_registry(registry_with(root_method=""), CHAIN_CONFIG)
        self.assertIsNone(drops[0].root_method)

    def test_missing_registry_file(self):
        with self.assertRaises(ConfigError):
            load_registry("/nonexistent/registry.json", CHAIN_CONFIG)


if __name__ == "__main__":
    unittest.main()
