"""Keccak-256 and ABI encoding tests.

The Keccak vectors are the canonical ones; the selector vectors are the
well-known Ethereum function ids, which proves we are not accidentally hashing
with SHA3-256 (whose domain byte differs).
"""

import unittest

from idlescan.abi import (
    AbiError,
    calldata,
    checksum,
    decode,
    encode,
    encode_packed,
    normalise_address,
    selector,
)
from idlescan.keccak import keccak256


class KeccakTests(unittest.TestCase):
    def test_known_vectors(self):
        self.assertEqual(
            keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )
        self.assertEqual(
            keccak256(b"abc").hex(),
            "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        )
        self.assertEqual(
            keccak256(b"The quick brown fox jumps over the lazy dog").hex(),
            "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15",
        )

    def test_long_input_crosses_rate_boundary(self):
        # 136 bytes is exactly the sponge rate: padding behaviour changes here.
        self.assertEqual(len(keccak256(b"a" * 136)), 32)
        self.assertNotEqual(keccak256(b"a" * 135), keccak256(b"a" * 136))
        self.assertEqual(len(keccak256(b"a" * 1000)), 32)

    def test_selectors(self):
        self.assertEqual(selector("transfer(address,uint256)").hex(), "a9059cbb")
        self.assertEqual(selector("balanceOf(address)").hex(), "70a08231")
        self.assertEqual(selector("isClaimed(uint256)").hex(), "9e34070f")


class AbiTests(unittest.TestCase):
    def test_uint_and_address_words(self):
        data = encode(["uint256", "address"], [1, "0x000000000000000000000000000000000000dEaD"])
        self.assertEqual(len(data), 64)
        self.assertTrue(data[:32].endswith(b"\x01"))
        self.assertEqual(data[32:].hex(), "0" * 24 + "000000000000000000000000000000000000dead")

    def test_roundtrip_static_and_dynamic(self):
        values = [7, "0x" + "11" * 20, True, b"\x22" * 32, "hello", b"\x00\xff"]
        types = ["uint256", "address", "bool", "bytes32", "string", "bytes"]
        decoded = decode(types, encode(types, values))
        self.assertEqual(decoded[0], 7)
        self.assertEqual(decoded[1], "0x" + "11" * 20)
        self.assertIs(decoded[2], True)
        self.assertEqual(decoded[3], b"\x22" * 32)
        self.assertEqual(decoded[4], "hello")
        self.assertEqual(decoded[5], b"\x00\xff")

    def test_bytes32_array(self):
        proof = ["0x" + "ab" * 32, "0x" + "cd" * 32]
        data = encode(["bytes32[]"], [proof])
        decoded = decode(["bytes32[]"], data)[0]
        self.assertEqual(decoded, [bytes.fromhex("ab" * 32), bytes.fromhex("cd" * 32)])

    def test_packed_encoding_is_shorter_for_addresses(self):
        packed = encode_packed(["uint256", "address", "uint256"], [0, "0x" + "11" * 20, 5])
        abi = encode(["uint256", "address", "uint256"], [0, "0x" + "11" * 20, 5])
        self.assertEqual(len(packed), 32 + 20 + 32)
        self.assertEqual(len(abi), 96)
        # They must differ: this is exactly the mistake that breaks merkle proofs.
        self.assertNotEqual(keccak256(packed), keccak256(abi))

    def test_calldata_selector(self):
        data = calldata("balanceOf(address)", ["address"], ["0x" + "11" * 20])
        self.assertTrue(data.startswith("0x70a08231"))
        self.assertEqual(len(data), 2 + 8 + 64)

    def test_uint_overflow_rejected(self):
        with self.assertRaises(AbiError):
            encode(["uint256"], [1 << 256])
        with self.assertRaises(AbiError):
            encode(["uint256"], [-1])

    def test_address_validation(self):
        self.assertEqual(normalise_address("0xD8DA6BF26964AF9D7EED9E03E53415D37AA96045"), "0xd8da6bf26964af9d7eed9e03e53415d37aa96045")
        with self.assertRaises(ValueError):
            normalise_address("0x1234")
        with self.assertRaises(ValueError):
            normalise_address("0x" + "zz" * 20)

    def test_eip55_checksum(self):
        self.assertEqual(
            checksum("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"),
            "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        )
        self.assertEqual(
            checksum("0xd8da6bf26964af9d7eed9e03e53415d37aa96045"),
            "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        )


if __name__ == "__main__":
    unittest.main()
