"""Merkle maths, cross-checked against Uniswap's own reference fixture.

``tests/fixtures/uniswap_complex_example.json`` is ``scripts/complex_example.json``
from github.com/Uniswap/merkle-distributor (MIT).  The expected root below was
produced by ethers v5 with the same algorithm their TypeScript generator uses
(``solidityPack`` leaves, sorted pair hashing, odd node promoted).  If our Python
implementation ever drifts, this test fails.
"""

import json
import unittest
from pathlib import Path

from idlescan.merkle import (
    build_proof,
    compute_root,
    leaf_hash,
    parse_amount,
    parse_dataset,
    verify_proof,
)

FIXTURE = Path(__file__).parent / "fixtures" / "uniswap_complex_example.json"
REFERENCE_ROOT = "4632a65752cc098dae02fef4772b32cc25caca9a56fc305a32fd94affb9440e5"


def _fixture_claims():
    raw = json.loads(FIXTURE.read_text())
    return [(account, int(value) * 10**18) for account, value in raw.items()]


class RootTests(unittest.TestCase):
    def test_matches_uniswap_reference_root(self):
        claims = _fixture_claims()
        leaves = [
            leaf_hash(index, account, amount, encoding="packed")
            for index, (account, amount) in enumerate(claims)
        ]
        self.assertEqual(compute_root(leaves).hex(), REFERENCE_ROOT)

    def test_packed_and_abi_leaves_differ(self):
        packed = leaf_hash(0, "0x" + "11" * 20, 100, encoding="packed")
        abi = leaf_hash(0, "0x" + "11" * 20, 100, encoding="abi")
        double = leaf_hash(0, "0x" + "11" * 20, 100, encoding="abi", double_hash=True)
        self.assertEqual(len({packed, abi, double}), 3)

    def test_single_leaf_tree_is_its_own_root(self):
        leaf = leaf_hash(0, "0x" + "22" * 20, 5)
        self.assertEqual(compute_root([leaf]), leaf)


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.claims = _fixture_claims()
        self.leaves = [
            leaf_hash(index, account, amount, encoding="packed")
            for index, (account, amount) in enumerate(self.claims)
        ]
        self.root = compute_root(self.leaves)

    def test_every_proof_verifies(self):
        for index in (0, 1, 42, len(self.claims) - 1):
            account, amount = self.claims[index]
            proof = build_proof(index, account, amount, self.claims, encoding="packed")
            self.assertTrue(
                verify_proof(index, account, amount, proof, self.root, encoding="packed"),
                f"proof failed for index {index}",
            )
            self.assertGreater(len(proof), 0)

    def test_wrong_amount_fails(self):
        account, amount = self.claims[3]
        proof = build_proof(3, account, amount, self.claims, encoding="packed")
        self.assertFalse(
            verify_proof(3, account, amount + 1, proof, self.root, encoding="packed")
        )

    def test_wrong_root_fails(self):
        account, amount = self.claims[0]
        proof = build_proof(0, account, amount, self.claims, encoding="packed")
        self.assertFalse(
            verify_proof(0, account, amount, proof, b"\x00" * 32, encoding="packed")
        )

    def test_encoding_must_match(self):
        account, amount = self.claims[5]
        proof = build_proof(5, account, amount, self.claims, encoding="packed")
        self.assertFalse(verify_proof(5, account, amount, proof, self.root, encoding="abi"))


class DatasetTests(unittest.TestCase):
    def test_parses_merkle_distributor_shape(self):
        payload = {
            "merkleRoot": "0x" + "ab" * 32,
            "tokenTotal": "0x64",
            "claims": {
                "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f": {
                    "index": 0,
                    "amount": "0x64",
                    "proof": [],
                }
            },
        }
        dataset = parse_dataset(payload)
        claim = dataset.get("0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f")
        self.assertIsNotNone(claim)
        self.assertEqual(claim.amount, 100)
        self.assertEqual(dataset.root, bytes.fromhex("ab" * 32))
        self.assertEqual(len(dataset), 1)

    def test_parses_list_shape(self):
        payload = {
            "claims": [
                {"account": "0x" + "11" * 20, "index": 1, "amount": "1000", "proof": []},
                {"account": "0x" + "22" * 20, "index": 2, "amount": "2000", "proof": []},
            ]
        }
        dataset = parse_dataset(payload)
        self.assertEqual(len(dataset), 2)
        self.assertEqual(dataset.get("0x" + "22" * 20).amount, 2000)

    def test_amount_formats(self):
        self.assertEqual(parse_amount(5), 5)
        self.assertEqual(parse_amount("5"), 5)
        self.assertEqual(parse_amount("0x10"), 16)
        self.assertEqual(parse_amount("1e3"), 1000)
        with self.assertRaises(ValueError):
            parse_amount("1.5")

    def test_rejects_missing_index(self):
        with self.assertRaises(ValueError):
            parse_dataset({"claims": {"0x" + "11" * 20: {"amount": "1", "proof": []}}})


if __name__ == "__main__":
    unittest.main()
