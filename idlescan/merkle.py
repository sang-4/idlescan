"""Merkle-distributor leaf/proof maths.

Compatible with the widely forked Uniswap ``merkle-distributor`` shape
(``keccak256(abi.encode(index, account, amount))`` leaves, sorted pair hashing,
odd nodes promoted a layer unchanged) plus the variants that double-hash the
leaf or use ``abi.encodePacked`` for it.

The important property for this tool: a proof fetched from *any* third party is
only trusted after it hashes to the root read directly from the distributor
contract on chain.  A malicious or stale eligibility API therefore cannot make
us report a fake unclaimed balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .abi import AbiError, encode, encode_packed, normalise_address
from .keccak import keccak256

LEAF_TYPES = ("uint256", "address", "uint256")


def parse_amount(value: Any) -> int:
    """Accept ``123``, ``"123"``, ``"0x7b"`` or ``"1.5e18"``-style amounts."""
    if isinstance(value, bool):
        raise ValueError("amount must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"non-integer amount {value!r}")
        return int(value)
    text = str(value).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    if "." in text or "e" in text.lower():
        from decimal import Decimal, InvalidOperation

        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"non-integer amount {value!r}") from None
        if number != number.to_integral_value():
            raise ValueError(f"non-integer amount {value!r}")
        return int(number)
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"non-integer amount {value!r}") from None


def leaf_hash(
    index: int,
    account: str,
    amount: int,
    *,
    encoding: str = "abi",
    double_hash: bool = False,
) -> bytes:
    """Hash one claim into a tree leaf."""
    account = normalise_address(account)
    if encoding == "abi":
        raw = encode(LEAF_TYPES, (index, account, amount))
    elif encoding == "packed":
        raw = encode_packed(LEAF_TYPES, (index, account, amount))
    else:
        raise ValueError(f"unknown leaf encoding {encoding!r}")
    digest = keccak256(raw)
    return keccak256(digest) if double_hash else digest


def pair_hash(left: bytes, right: bytes, *, sort_pairs: bool = True) -> bytes:
    if sort_pairs and left > right:
        left, right = right, left
    return keccak256(left + right)


def compute_root(leaves: Iterable[bytes], *, sort_pairs: bool = True, sort_leaves: bool = True) -> bytes:
    nodes = list(leaves)
    if not nodes:
        raise ValueError("cannot build a merkle tree with no leaves")
    if sort_leaves:
        nodes.sort()
        deduped = [nodes[0]]
        for node in nodes[1:]:
            if node != deduped[-1]:
                deduped.append(node)
        nodes = deduped

    while len(nodes) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(nodes), 2):
            if i + 1 < len(nodes):
                nxt.append(pair_hash(nodes[i], nodes[i + 1], sort_pairs=sort_pairs))
            else:
                nxt.append(nodes[i])
        nodes = nxt
    return nodes[0]


def verify_proof(
    index: int,
    account: str,
    amount: int,
    proof: Sequence[bytes],
    root: bytes,
    *,
    encoding: str = "abi",
    double_hash: bool = False,
    sort_pairs: bool = True,
) -> bool:
    node = leaf_hash(index, account, amount, encoding=encoding, double_hash=double_hash)
    for sibling in proof:
        node = pair_hash(node, sibling, sort_pairs=sort_pairs)
    return node == root


def build_proof(
    index: int,
    account: str,
    amount: int,
    claims: Sequence[tuple[str, int]],
    *,
    encoding: str = "abi",
    double_hash: bool = False,
    sort_pairs: bool = True,
) -> list[bytes]:
    """Generate a proof locally; ``claims`` is ``[(account, amount), ...]``.

    Indexes follow list order, matching how merkle-distributor generators assign
    them.
    """
    target = leaf_hash(index, account, amount, encoding=encoding, double_hash=double_hash)
    nodes = [
        leaf_hash(i, acct, amt, encoding=encoding, double_hash=double_hash) for i, (acct, amt) in enumerate(claims)
    ]
    nodes.sort()
    proof: list[bytes] = []
    position = nodes.index(target)
    while len(nodes) > 1:
        sibling = position ^ 1
        if sibling < len(nodes):
            proof.append(nodes[sibling])
        nxt: list[bytes] = []
        for i in range(0, len(nodes), 2):
            if i + 1 < len(nodes):
                nxt.append(pair_hash(nodes[i], nodes[i + 1], sort_pairs=sort_pairs))
            else:
                nxt.append(nodes[i])
        nodes = nxt
        position //= 2
    return proof


@dataclass(frozen=True)
class Claim:
    index: int
    amount: int
    proof: tuple[bytes, ...]

    @property
    def amount_hex(self) -> str:
        return hex(self.amount)


@dataclass
class ClaimDataset:
    """A claim set keyed by address, optionally with its published root."""

    claims: Mapping[str, Claim]
    root: bytes | None = None
    source: str | None = None
    encoding: str = "abi"
    double_hash: bool = False
    sort_pairs: bool = True
    provenance: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, source: str | None = None) -> "ClaimDataset":
        return parse_dataset(data, source=source)

    def get(self, address: str) -> Claim | None:
        return self.claims.get(normalise_address(address))

    def items(self) -> Iterable[tuple[str, Claim]]:
        return self.claims.items()

    def __len__(self) -> int:
        return len(self.claims)

    def close(self) -> None:
        close = getattr(self.claims, "close", None)
        if callable(close):
            close()

    def computed_root(self) -> bytes:
        leaves = [
            leaf_hash(claim.index, address, claim.amount, encoding=self.encoding, double_hash=self.double_hash)
            for address, claim in self.claims.items()
        ]
        return compute_root(leaves, sort_pairs=self.sort_pairs)


def _proof_bytes(items: Iterable[Any]) -> tuple[bytes, ...]:
    out = []
    for item in items:
        raw = item if isinstance(item, (bytes, bytearray)) else bytes.fromhex(str(item).removeprefix("0x"))
        if len(raw) != 32:
            raise ValueError(f"proof element must be 32 bytes, got {len(raw)}")
        out.append(bytes(raw))
    return tuple(out)


def parse_dataset(data: Mapping[str, Any], *, source: str | None = None) -> ClaimDataset:
    """Parse the common merkle-distributor JSON shapes.

    Accepts ``{"claims": {addr: {index, amount, proof}}}`` (with optional
    ``merkleRoot``), the bare ``{addr: {index, amount, proof}}`` variant, and the
    array form ``{"claims": [{account, index, amount, proof}]}``.
    """
    claims_raw = data.get("claims", data)
    encoding = str(data.get("leafEncoding", data.get("leaf_encoding", "abi")))
    double_hash = bool(data.get("doubleHash", data.get("double_hash", False)))
    sort_pairs = bool(data.get("sortPairs", data.get("sort_pairs", True)))

    claims: dict[str, Claim] = {}
    if isinstance(claims_raw, Mapping):
        items = [(address, entry) for address, entry in claims_raw.items() if isinstance(entry, Mapping)]
    elif isinstance(claims_raw, list):
        items = [
            (entry.get("account") or entry.get("address"), entry) for entry in claims_raw if isinstance(entry, Mapping)
        ]
    else:
        raise ValueError("dataset has no claims section")

    for address, entry in items:
        if not address:
            continue
        try:
            claim = Claim(
                index=parse_amount(entry["index"]),
                amount=parse_amount(entry["amount"]),
                proof=_proof_bytes(entry.get("proof") or entry.get("merkleProof") or ()),
            )
        except (KeyError, ValueError, AbiError) as exc:
            raise ValueError(f"bad claim entry for {address}: {exc}") from exc
        claims[normalise_address(address)] = claim

    root = None
    raw_root = data.get("merkleRoot") or data.get("merkle_root") or data.get("root")
    if raw_root:
        root = bytes.fromhex(str(raw_root).removeprefix("0x"))
        if len(root) != 32:
            raise ValueError("merkleRoot must be 32 bytes")

    return ClaimDataset(
        claims=claims,
        root=root,
        source=source,
        encoding=encoding,
        double_hash=double_hash,
        sort_pairs=sort_pairs,
    )
