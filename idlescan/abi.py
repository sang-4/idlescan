"""Tiny ABI encoder/decoder.

Only the shapes this tool needs are implemented: static words (uint/int N,
address, bool, bytesN) and the dynamic types bytes, string and arrays of static
values.  That is enough for ERC-20 calls, merkle-distributor calls and the view
functions we probe in the registry.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .keccak import keccak256

WORD = 32

_STATIC_PREFIXES = ("uint", "int", "address", "bool", "bytes", "byte")


class AbiError(ValueError):
    """Raised for unsupported types or malformed calldata."""


def _is_dynamic(type_name: str) -> bool:
    return type_name in ("bytes", "string") or type_name.endswith("[]")


def _encode_word(type_name: str, value: Any) -> bytes:
    if type_name.startswith("uint"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise AbiError(f"{type_name} expects int, got {type(value).__name__}")
        if value < 0:
            raise AbiError(f"{type_name} cannot be negative")
        if value >= 1 << 256:
            raise AbiError(f"{type_name} overflows 256 bits")
        return value.to_bytes(WORD, "big")

    if type_name.startswith("int"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise AbiError(f"{type_name} expects int")
        return value.to_bytes(WORD, "big", signed=True)

    if type_name == "address":
        if not isinstance(value, str):
            raise AbiError("address expects a hex string")
        raw = bytes.fromhex(value.removeprefix("0x"))
        if len(raw) != 20:
            raise AbiError(f"address must be 20 bytes, got {len(raw)}")
        return b"\x00" * 12 + raw

    if type_name == "bool":
        return (1 if value else 0).to_bytes(WORD, "big")

    if type_name.startswith("bytes") and type_name != "bytes":
        size = int(type_name[5:])
        raw = value if isinstance(value, (bytes, bytearray)) else bytes.fromhex(
            str(value).removeprefix("0x")
        )
        if len(raw) > size:
            raise AbiError(f"{type_name} got {len(raw)} bytes")
        return bytes(raw).ljust(WORD, b"\x00")

    raise AbiError(f"unsupported static type {type_name!r}")


def encode(types: Sequence[str], values: Sequence[Any]) -> bytes:
    """ABI-encode ``values`` according to ``types`` (standard head/tail layout)."""
    if len(types) != len(values):
        raise AbiError(f"{len(types)} types but {len(values)} values")

    heads: list[bytes] = []
    tails: list[bytes] = []
    head_size = WORD * len(types)

    for type_name, value in zip(types, values, strict=False):
        if not _is_dynamic(type_name):
            heads.append(_encode_word(type_name, value))
            continue

        if type_name in ("bytes", "string"):
            raw = value.encode() if isinstance(value, str) else bytes(value)
            body = len(raw).to_bytes(WORD, "big") + raw
        else:
            element_type = type_name[:-2]
            if element_type != "bytes32":
                raise AbiError(f"array element type {element_type!r} not supported")
            items = list(value)
            body = len(items).to_bytes(WORD, "big") + b"".join(
                _encode_word("bytes32", item) for item in items
            )
        body += b"\x00" * ((WORD - len(body) % WORD) % WORD)
        heads.append((head_size + sum(len(t) for t in tails)).to_bytes(WORD, "big"))
        tails.append(body)

    return b"".join(heads) + b"".join(tails)


def encode_packed(types: Sequence[str], values: Sequence[Any]) -> bytes:
    """Solidity ``abi.encodePacked`` for the static types used in merkle leaves."""
    out = bytearray()
    for type_name, value in zip(types, values, strict=False):
        if _is_dynamic(type_name):
            raise AbiError("encode_packed does not support dynamic types here")
        word = _encode_word(type_name, value)
        if type_name == "address":
            out += word[12:]
        elif type_name == "bool":
            out += b"\x01" if value else b"\x00"
        elif type_name.startswith("bytes") and type_name != "bytes":
            size = int(type_name[5:])
            out += word[:size]
        elif type_name.startswith("uint") or type_name.startswith("int"):
            bits = 256 if type_name in ("uint", "int") else int(
                "".join(ch for ch in type_name if ch.isdigit())
            )
            out += word[WORD - bits // 8 :]
        else:
            raise AbiError(f"encode_packed does not support {type_name!r}")
    return bytes(out)


def selector(signature: str) -> bytes:
    """4-byte selector for e.g. ``balanceOf(address)``."""
    return keccak256(signature.encode())[:4]


def calldata(signature: str, types: Sequence[str] = (), values: Sequence[Any] = ()) -> str:
    return "0x" + (selector(signature) + encode(types, values)).hex()


def _read_word(data: bytes, offset: int) -> bytes:
    if offset + WORD > len(data):
        raise AbiError("calldata too short")
    return data[offset : offset + WORD]


def _decode_static(type_name: str, word: bytes) -> Any:
    if type_name.startswith("uint"):
        return int.from_bytes(word, "big")
    if type_name.startswith("int"):
        return int.from_bytes(word, "big", signed=True)
    if type_name == "address":
        return "0x" + word[12:].hex()
    if type_name == "bool":
        return int.from_bytes(word, "big") != 0
    if type_name.startswith("bytes"):
        return word
    raise AbiError(f"unsupported static type {type_name!r}")


def decode(types: Sequence[str], data: bytes) -> list[Any]:
    if isinstance(data, str):
        data = bytes.fromhex(data.removeprefix("0x"))
    out: list[Any] = []
    for index, type_name in enumerate(types):
        head = _read_word(data, index * WORD)
        if not _is_dynamic(type_name):
            out.append(_decode_static(type_name, head))
            continue

        offset = int.from_bytes(head, "big")
        length = int.from_bytes(_read_word(data, offset), "big")
        start = offset + WORD
        if type_name in ("bytes", "string"):
            raw = data[start : start + length]
            if len(raw) != length:
                raise AbiError("truncated dynamic value")
            out.append(raw.decode("utf-8", "replace") if type_name == "string" else raw)
        else:
            element_type = type_name[:-2]
            out.append(
                [
                    _decode_static(element_type, _read_word(data, start + i * WORD))
                    for i in range(length)
                ]
            )
    return out


def decode_one(type_name: str, data: bytes | str) -> Any:
    try:
        return decode([type_name], data)[0]
    except AbiError:
        return None


def normalise_address(value: str) -> str:
    """Lower-case, 0x-prefixed, checksum-agnostic address."""
    raw = value.strip()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    body = raw[2:]
    if len(body) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in body):
        raise ValueError(f"not a valid EVM address: {value!r}")
    return "0x" + body.lower()


def short(address: str, head: int = 6, tail: int = 4) -> str:
    address = normalise_address(address)
    return f"{address[: 2 + head]}...{address[-tail:]}"


def checksum(address: str) -> str:
    """EIP-55 mixed-case checksum encoding."""
    plain = normalise_address(address)[2:]
    digest = keccak256(plain.encode()).hex()
    return "0x" + "".join(
        ch.upper() if ch in "abcdef" and int(digest[i], 16) >= 8 else ch
        for i, ch in enumerate(plain)
    )


def iter_addresses(values: Iterable[str]) -> list[str]:
    return [normalise_address(value) for value in values]
