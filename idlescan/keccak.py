"""Dependency-free Keccak-256 (the pre-standard padding Ethereum uses).

Python's hashlib only ships SHA3-256, which differs from Keccak-256 in its
domain-separation byte (0x06 vs 0x01), so every Ethereum function selector and
every merkle leaf computed with hashlib would be wrong.  Rather than pull in
pycryptodome/eth-hash we implement the permutation directly; it is ~40 lines and
is covered by the test vectors in ``tests/test_keccak_abi.py``.
"""

from __future__ import annotations

RATE_BYTES = 136  # 1088-bit rate for a 256-bit output

_MASK = (1 << 64) - 1

_ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)

# Rotation offsets, indexed [x][y].
_ROTATIONS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotl(value: int, shift: int) -> int:
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _keccak_f1600(state: list[int]) -> None:
    for rnd in range(24):
        # theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        # rho + pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(state[x + 5 * y], _ROTATIONS[x][y])

        # chi
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y] & _MASK) & b[(x + 2) % 5 + 5 * y])

        # iota
        state[0] ^= _ROUND_CONSTANTS[rnd]


def _absorb(state: list[int], block: bytes) -> None:
    for i in range(0, RATE_BYTES, 8):
        state[i // 8] ^= int.from_bytes(block[i : i + 8], "little")


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte Keccak-256 digest of ``data``."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("keccak256 expects bytes")
    payload = bytes(data)
    state = [0] * 25

    padded = bytearray(payload)
    pad_len = RATE_BYTES - (len(padded) % RATE_BYTES)
    if pad_len == 1:
        padded.append(0x81)
    else:
        padded.append(0x01)
        padded.extend(b"\x00" * (pad_len - 2))
        padded.append(0x80)

    for offset in range(0, len(padded), RATE_BYTES):
        _absorb(state, bytes(padded[offset : offset + RATE_BYTES]))
        _keccak_f1600(state)

    return b"".join(state[i].to_bytes(8, "little") for i in range(4))


def keccak_hex(data: bytes) -> str:
    return "0x" + keccak256(data).hex()
