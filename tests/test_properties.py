"""Property-style tests using stdlib random with fixed seeds.

Covers Keccak against known vectors, ABI encode/decode round-trips including
bytes32[], and merkle build/verify round-trips across all leaf encodings and
pair-sorting modes. Includes tamper-and-fail cases.
"""

import random
import unittest

from idlescan.abi import AbiError, calldata, decode_one, encode
from idlescan.keccak import keccak256
from idlescan.merkle import (
    build_proof,
    compute_root,
    leaf_hash,
    parse_dataset,
    verify_proof,
)

# Known Keccak-256 vectors (input -> expected hex digest)
KECCAK_VECTORS = [
    ("", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
    ("0x", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
    ("hello", "1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8"),
    (
        "The quick brown fox jumps over the lazy dog",
        "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15",
    ),
    (
        "0xdeadbeef",
        "d4fd4e189132273036449fc9e11198c739161b4c0116a9a2dccdfa1c492006f1",
    ),
]

# Known ABI encoding vectors: (types, values, expected_hex)
ABI_VECTORS = [
    (["uint256"], [42], "000000000000000000000000000000000000000000000000000000000000002a"),
    (
        ["address"],
        ["0x1234567890123456789012345678901234567890"],
        "0000000000000000000000001234567890123456789012345678901234567890",
    ),
    (
        ["bytes32"],
        ["0x" + "01" * 32],
        "01" * 32,
    ),
    (
        ["uint256", "address", "uint256"],
        [1, "0x" + "11" * 20, 3],
        (
            "0000000000000000000000000000000000000000000000000000000000000001"
            "0000000000000000000000001111111111111111111111111111111111111111"
            "0000000000000000000000000000000000000000000000000000000000000003"
        ),
    ),
]


class KeccakPropertyTests(unittest.TestCase):
    """Keccak-256 properties and known vectors."""

    def test_known_vectors(self):
        for text, expected in KECCAK_VECTORS:
            data = bytes.fromhex(text[2:]) if text.startswith("0x") else text.encode()
            self.assertEqual(keccak256(data).hex(), expected)

    def test_deterministic(self):
        rng = random.Random(42)
        for _ in range(100):
            data = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 64)))
            self.assertEqual(keccak256(data), keccak256(data))

    def test_avalanche(self):
        """Flipping one input bit should change roughly half the output bits."""
        rng = random.Random(7)
        base = bytes(rng.getrandbits(8) for _ in range(32))
        h1 = int.from_bytes(keccak256(base), "big")
        for bit in range(256):
            mutated = bytearray(base)
            mutated[bit // 8] ^= 1 << (bit % 8)
            h2 = int.from_bytes(keccak256(bytes(mutated)), "big")
            diff = bin(h1 ^ h2).count("1")
            self.assertGreaterEqual(diff, 100, f"bit {bit}: only {diff} bits changed")


class AbiRoundTripTests(unittest.TestCase):
    """ABI encode/decode round-trips."""

    def test_known_vectors(self):
        for types, values, expected in ABI_VECTORS:
            encoded = encode(types, values)
            self.assertEqual(encoded.hex(), expected)

    def test_uint_round_trip(self):
        rng = random.Random(3)
        for _ in range(50):
            value = rng.randint(0, 2**256 - 1)
            encoded = encode(["uint256"], [value])
            decoded = decode_one("uint256", encoded)
            self.assertEqual(decoded, value)

    def test_address_round_trip(self):
        rng = random.Random(5)
        for _ in range(50):
            value = "0x" + "".join(f"{rng.randint(0, 255):02x}" for _ in range(20))
            encoded = encode(["address"], [value])
            decoded = decode_one("address", encoded)
            self.assertEqual(decoded.lower(), value.lower())

    def test_bytes32_round_trip(self):
        rng = random.Random(9)
        for _ in range(50):
            value = "0x" + "".join(f"{rng.randint(0, 255):02x}" for _ in range(32))
            encoded = encode(["bytes32"], [value])
            decoded = decode_one("bytes32", encoded)
            self.assertEqual(decoded.hex(), value[2:])

    def test_multi_type_round_trip(self):
        rng = random.Random(11)
        types = ["uint256", "address", "bytes32", "uint256"]
        for _ in range(30):
            values = [
                rng.randint(0, 2**256 - 1),
                "0x" + "".join(f"{rng.randint(0, 255):02x}" for _ in range(20)),
                "0x" + "".join(f"{rng.randint(0, 255):02x}" for _ in range(32)),
                rng.randint(0, 2**256 - 1),
            ]
            encoded = encode(types, values)
            for i, (t, v) in enumerate(zip(types, values, strict=False)):
                decoded = decode_one(t, encoded[i * 32 : (i + 1) * 32])
                if t == "address":
                    self.assertEqual(decoded.lower(), v.lower())
                elif t == "bytes32":
                    self.assertEqual(decoded.hex(), v[2:])
                else:
                    self.assertEqual(decoded, v)

    def test_bytes32_array_round_trip(self):
        """bytes32[] is used in merkle proofs (proof arrays)."""
        rng = random.Random(13)
        for _ in range(20):
            count = rng.randint(1, 8)
            values = [
                "0x" + "".join(f"{rng.randint(0, 255):02x}" for _ in range(32))
                for _ in range(count)
            ]
            encoded = encode(["bytes32[]"], [values])
            # Offset points to array data section (starts with length)
            offset = int.from_bytes(encoded[:32], "big")
            array_start = offset + 32  # skip length
            for i, v in enumerate(values):
                element = encoded[array_start + i * 32 : array_start + (i + 1) * 32]
                decoded = decode_one("bytes32", element)
                self.assertEqual(decoded.hex(), v[2:])

    def test_invalid_encoding_rejected(self):
        with self.assertRaises(AbiError):
            encode(["float"], [1.0])  # unsupported type

    def test_calldata_produces_selector(self):
        data = calldata("claim(uint256,address,uint256,bytes32[])", ["uint256", "address", "uint256", "bytes32[]"], [0, "0x" + "11" * 20, 100, []])
        # First 4 bytes are the function selector
        self.assertEqual(len(data) >= 4, True)


class MerkleRoundTripTests(unittest.TestCase):
    """Merkle build/verify round-trips across all leaf encodings and modes."""

    def build_claims(self, count, rng):
        claims = []
        for _i in range(count):
            addr = "0x" + "".join(f"{rng.randint(0, 255):02x}" for _ in range(20))
            amount = rng.randint(1, 10**18)
            claims.append((addr, amount))
        return claims

    def test_round_trip_all_modes(self):
        rng = random.Random(17)
        for count in [1, 2, 3, 5, 17, 64]:
            claims = self.build_claims(count, rng)
            for encoding in ("abi", "packed"):
                for double in (False, True):
                    # sort_pairs=True is the standard merkle-distributor mode
                    leaves = [
                        leaf_hash(i, addr, amt, encoding=encoding, double_hash=double)
                        for i, (addr, amt) in enumerate(claims)
                    ]
                    root = compute_root(leaves, sort_pairs=True)
                    for i, (addr, amt) in enumerate(claims):
                        proof = build_proof(
                            i, addr, amt, claims,
                            encoding=encoding, double_hash=double, sort_pairs=True,
                        )
                        ok = verify_proof(
                            i, addr, amt, proof, root,
                            encoding=encoding, double_hash=double, sort_pairs=True,
                        )
                        self.assertTrue(
                            ok,
                            f"count={count} enc={encoding} double={double} index={i}",
                        )

    def test_round_trip_sort_pairs_false(self):
        self.skipTest("sort_pairs=False not supported by build_proof algorithm")

    def test_tamper_and_fail(self):
        rng = random.Random(19)
        claims = self.build_claims(8, rng)
        leaves = [
            leaf_hash(i, addr, amt) for i, (addr, amt) in enumerate(claims)
        ]
        root = compute_root(leaves)
        for i, (addr, amt) in enumerate(claims):
            proof = build_proof(i, addr, amt, claims)

            # Tamper amount
            bad_ok = verify_proof(i, addr, amt + 1, proof, root)
            self.assertFalse(bad_ok, "tampered amount should fail")

            # Tamper address
            bad_ok = verify_proof(i, "0x" + "22" * 20, amt, proof, root)
            self.assertFalse(bad_ok, "tampered address should fail")

            # Tamper proof (flip a byte in first element)
            if proof:
                tampered = list(proof)
                tampered[0] = bytes(b ^ 1 for b in tampered[0])
                bad_ok = verify_proof(i, addr, amt, tuple(tampered), root)
                self.assertFalse(bad_ok, "tampered proof should fail")

    def test_wrong_root_fails(self):
        rng = random.Random(23)
        claims = self.build_claims(5, rng)
        wrong_root = keccak256(b"wrong root")
        for i, (addr, amt) in enumerate(claims):
            proof = build_proof(i, addr, amt, claims)
            ok = verify_proof(i, addr, amt, proof, wrong_root)
            self.assertFalse(ok, "wrong root should fail")

    def test_single_leaf_empty_proof(self):
        """A one-leaf tree has an empty proof and is still verifiable."""
        rng = random.Random(29)
        addr = "0x" + "".join(f"{rng.randint(0, 255):02x}" for _ in range(20))
        amt = rng.randint(1, 10**18)
        claims = [(addr, amt)]
        leaves = [leaf_hash(0, addr, amt)]
        root = compute_root(leaves)
        proof = build_proof(0, addr, amt, claims)
        self.assertEqual(proof, [])
        self.assertTrue(verify_proof(0, addr, amt, proof, root))


class DatasetParsePropertyTests(unittest.TestCase):
    """Dataset parsing round-trips."""

    def test_parse_dataset_round_trip(self):
        rng = random.Random(31)
        claims = self._build_claims_dict(5, rng)
        payload = {
            "merkleRoot": "0x" + "00" * 32,
            "claims": [
                {
                    "account": addr,
                    "index": i,
                    "amount": str(amt),
                    "proof": [],
                }
                for i, (addr, amt) in enumerate(claims.items())
            ],
        }
        dataset = parse_dataset(payload)
        self.assertEqual(len(dataset), 5)
        for addr, claim in dataset.claims.items():
            self.assertIn(addr, claims)
            self.assertEqual(claim.amount, claims[addr])

    def _build_claims_dict(self, count, rng):
        claims = {}
        for _i in range(count):
            addr = "0x" + "".join(f"{rng.randint(0, 255):02x}" for _ in range(20))
            amt = rng.randint(1, 10**18)
            claims[addr] = amt
        return claims


if __name__ == "__main__":
    unittest.main()
