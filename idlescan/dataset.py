"""Dataset loading, streaming parsing and provenance.

Claim datasets are third-party inputs.  A proof is only trusted after it hashes
to the root read from the distributor, but an operator still needs to know where
a file came from, when it was published, and whether it is old enough to be
suspicious.  This module computes that metadata and provides a streaming parser
for the common array/object shapes so a large distribution file does not have to
be held in memory in full.

Above ``STREAM_THRESHOLD_BYTES`` the parsed claims are written to a temporary
on-disk SQLite index (D-010).  The mapping interface stays the same, so callers
do not need to know which representation was chosen.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .abi import normalise_address
from .merkle import Claim, ClaimDataset, parse_amount, _proof_bytes

STREAM_THRESHOLD_BYTES = 8 * 1024 * 1024
MAX_DATASET_BYTES = 512 * 1024 * 1024
_META_KEYS = {
    "merkleRoot",
    "merkle_root",
    "root",
    "leafEncoding",
    "leaf_encoding",
    "doubleHash",
    "double_hash",
    "sortPairs",
    "sort_pairs",
    "version",
    "name",
}
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


class DatasetError(ValueError):
    """A dataset file is unreadable, malformed or too large."""


class DatasetIndex(Mapping[str, Claim]):
    """A disk-backed claim mapping used for large datasets."""

    def __init__(self, *, directory: Path | None = None) -> None:
        handle = tempfile.NamedTemporaryFile(
            prefix="idlescan-dataset-", suffix=".db", dir=directory, delete=False
        )
        self.path = Path(handle.name)
        handle.close()
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA journal_mode=OFF")
        self._connection.execute("PRAGMA synchronous=OFF")
        self._connection.execute(
            "CREATE TABLE claims(address TEXT PRIMARY KEY, idx TEXT NOT NULL, "
            "amount TEXT NOT NULL, proof TEXT NOT NULL)"
        )

    def put(self, address: str, claim: Claim) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO claims(address, idx, amount, proof) VALUES(?, ?, ?, ?)",
            (
                address,
                str(claim.index),
                str(claim.amount),
                ",".join(item.hex() for item in claim.proof),
            ),
        )

    def commit(self) -> None:
        self._connection.commit()

    def __getitem__(self, address: str) -> Claim:
        row = self._connection.execute(
            "SELECT idx, amount, proof FROM claims WHERE address = ?", (address,)
        ).fetchone()
        if row is None:
            raise KeyError(address)
        return Claim(
            index=int(row[0]),
            amount=int(row[1]),
            proof=tuple(bytes.fromhex(item) for item in row[2].split(",") if item),
        )

    def get(self, address: str, default: Any = None) -> Claim | None:
        try:
            return self[address]
        except KeyError:
            return default

    def __iter__(self) -> Iterator[str]:
        for row in self._connection.execute("SELECT address FROM claims ORDER BY address"):
            yield row[0]

    def __len__(self) -> int:
        return int(self._connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0])

    def items(self) -> Iterator[tuple[str, Claim]]:
        for row in self._connection.execute(
            "SELECT address, idx, amount, proof FROM claims ORDER BY address"
        ):
            yield row[0], Claim(
                index=int(row[1]),
                amount=int(row[2]),
                proof=tuple(bytes.fromhex(item) for item in row[3].split(",") if item),
            )

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            try:
                self.path.unlink()
            except OSError:
                pass

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


class _JsonStream:
    """Incremental JSON reader that never holds the whole document in memory."""

    def __init__(self, handle, chunk_size: int = 1024 * 1024) -> None:
        self.handle = handle
        self.chunk_size = chunk_size
        self.buffer = ""
        self.pos = 0
        self.eof = False

    def _fill(self) -> bool:
        if self.eof:
            return False
        chunk = self.handle.read(self.chunk_size)
        if not chunk:
            self.eof = True
            return False
        self.buffer = self.buffer[self.pos :] + chunk
        self.pos = 0
        return True

    def _skip_ws(self) -> None:
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos] in " \t\r\n":
                self.pos += 1
            if self.pos < len(self.buffer):
                return
            if not self._fill():
                return

    def peek(self) -> str:
        self._skip_ws()
        if self.pos >= len(self.buffer):
            raise DatasetError("unexpected end of JSON")
        return self.buffer[self.pos]

    def expect(self, character: str) -> None:
        if self.peek() != character:
            raise DatasetError(f"expected {character!r} in dataset JSON")
        self.pos += 1

    def value(self) -> Any:
        decoder = json.JSONDecoder()
        while True:
            self._skip_ws()
            try:
                value, end = decoder.raw_decode(self.buffer, self.pos)
            except json.JSONDecodeError:
                if not self._fill():
                    raise DatasetError("invalid or truncated JSON in dataset") from None
                continue
            self.pos = end
            return value

    def string(self) -> str:
        value = self.value()
        if not isinstance(value, str):
            raise DatasetError("expected a JSON object key")
        return value


def _claim_from_entry(address: str, entry: Any) -> Claim:
    if not isinstance(entry, Mapping):
        raise DatasetError(f"bad claim entry for {address}: expected an object")
    try:
        return Claim(
            index=parse_amount(entry["index"]),
            amount=parse_amount(entry["amount"]),
            proof=_proof_bytes(entry.get("proof") or entry.get("merkleProof") or ()),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise DatasetError(f"bad claim entry for {address}: {exc}") from exc


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DatasetProvenance:
    drop_id: str
    filename: str
    sha256: str
    byte_size: int
    fetched_at: str
    entry_count: int
    root: str | None = None
    source_url: str | None = None
    root_verified: bool = False
    block_number: int | None = None
    tool_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "drop_id": self.drop_id,
            "filename": self.filename,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "fetched_at": self.fetched_at,
            "entry_count": self.entry_count,
            "root": self.root,
            "source_url": self.source_url,
            "root_verified": self.root_verified,
            "block_number": self.block_number,
            "tool_version": self.tool_version,
        }


def dataset_provenance(
    path: Path,
    *,
    drop_id: str,
    root: bytes | None = None,
    source_url: str | None = None,
    root_verified: bool = False,
    block_number: int | None = None,
    entry_count: int = 0,
    tool_version: str = "",
) -> DatasetProvenance:
    """Describe a local dataset without trusting anything inside it."""
    stat = path.stat()
    fetched = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(microsecond=0)
    return DatasetProvenance(
        drop_id=drop_id,
        filename=path.name,
        sha256=file_sha256(path),
        byte_size=stat.st_size,
        fetched_at=fetched.isoformat(),
        entry_count=entry_count,
        root="0x" + root.hex() if root else None,
        source_url=source_url,
        root_verified=root_verified,
        block_number=block_number,
        tool_version=tool_version,
    )


def is_stale(provenance: DatasetProvenance, stale_days: int, *, now: datetime | None = None) -> bool:
    if stale_days <= 0:
        return False
    moment = now or datetime.now(timezone.utc)
    try:
        fetched = datetime.fromisoformat(provenance.fetched_at)
    except ValueError:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (moment - fetched).days > stale_days


def _read_claims_array(stream: _JsonStream) -> Iterator[Any]:
    stream.expect("[")
    if stream.peek() == "]":
        stream.pos += 1
        return
    while True:
        yield stream.value()
        character = stream.peek()
        if character == ",":
            stream.pos += 1
            continue
        if character == "]":
            stream.pos += 1
            return
        raise DatasetError("expected ',' or ']' in claims array")


def _read_bare_claims(stream: _JsonStream, first_key: str) -> Iterator[tuple[str, Any]]:
    """Yield entries from a top-level ``{address: claim}`` object."""
    key = first_key
    while True:
        yield key, stream.value()
        character = stream.peek()
        if character == ",":
            stream.pos += 1
            key = stream.string()
            stream.expect(":")
            continue
        if character == "}":
            stream.pos += 1
            return
        raise DatasetError("expected ',' or '}' in claims object")


def _read_claims_object(
    stream: _JsonStream,
    first_key: str | None = None,
    *,
    colon_consumed: bool = False,
) -> Iterator[tuple[str, Any]]:
    stream.expect("{")
    if stream.peek() == "}":
        stream.pos += 1
        return
    key = first_key
    while True:
        if key is None:
            key = stream.string()
            stream.expect(":")
        elif not colon_consumed:
            stream.expect(":")
        yield key, stream.value()
        character = stream.peek()
        if character == ",":
            stream.pos += 1
            key = None
            continue
        if character == "}":
            stream.pos += 1
            return
        raise DatasetError("expected ',' or '}' in claims object")


def _stream_dataset(path: Path, *, drop_id: str, use_index: bool) -> ClaimDataset:
    store: Any = DatasetIndex() if use_index else {}
    meta: dict[str, Any] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            stream = _JsonStream(handle)
            stream.expect("{")
            if stream.peek() == "}":
                raise DatasetError("dataset is empty")
            first = True
            while True:
                key = stream.string()
                stream.expect(":")
                if key == "claims":
                    if stream.peek() == "[":
                        for entry in _read_claims_array(stream):
                            address = entry.get("account") or entry.get("address") if isinstance(entry, Mapping) else None
                            if address:
                                _store_claim(store, address, _claim_from_entry(str(address), entry))
                    elif stream.peek() == "{":
                        for address, entry in _read_claims_object(stream):
                            _store_claim(store, address, _claim_from_entry(address, entry))
                    else:
                        raise DatasetError("claims must be an array or object")
                elif first and _ADDRESS.match(str(key)) and key not in _META_KEYS:
                    # Bare ``{address: claim}`` shape with no metadata wrapper.
                    for address, entry in _read_bare_claims(stream, key):
                        _store_claim(store, address, _claim_from_entry(address, entry))
                    first = False
                    break
                else:
                    meta[key] = stream.value()
                first = False
                character = stream.peek()
                if character == ",":
                    stream.pos += 1
                    continue
                if character == "}":
                    stream.pos += 1
                    break
                raise DatasetError("expected ',' or '}' at the top level of the dataset")
        if isinstance(store, DatasetIndex):
            store.commit()
        raw_root = meta.get("merkleRoot") or meta.get("merkle_root") or meta.get("root")
        root = None
        if raw_root:
            try:
                root = bytes.fromhex(str(raw_root).removeprefix("0x"))
            except ValueError as exc:
                raise DatasetError(f"merkleRoot is not hex: {exc}") from exc
            if len(root) != 32:
                raise DatasetError("merkleRoot must be 32 bytes")
        return ClaimDataset(
            claims=store,
            root=root,
            source=str(path),
            encoding=str(meta.get("leafEncoding") or meta.get("leaf_encoding") or "abi"),
            double_hash=bool(meta.get("doubleHash") or meta.get("double_hash") or False),
            sort_pairs=bool(meta.get("sortPairs", meta.get("sort_pairs", True))),
        )
    except Exception:
        if isinstance(store, DatasetIndex):
            store.close()
        raise


def _store_claim(store: Any, address: str, claim: Claim) -> None:
    normalised = normalise_address(address)
    if isinstance(store, DatasetIndex):
        store.put(normalised, claim)
    else:
        store[normalised] = claim


def parse_dataset_file(
    path: Path | str,
    *,
    drop_id: str = "",
    stream_threshold: int = STREAM_THRESHOLD_BYTES,
    max_bytes: int = MAX_DATASET_BYTES,
) -> ClaimDataset:
    """Read a dataset from disk, streaming large array/object claim lists.

    Small files take the simple ``json.loads`` path.  Large files are decoded
    incrementally and, above the threshold, indexed on disk.
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise DatasetError(f"cannot read dataset: {exc}") from exc
    if size > max_bytes:
        raise DatasetError(
            f"dataset is {size} bytes, above the {max_bytes}-byte safety cap"
        )
    if size <= stream_threshold:
        try:
            payload = json.loads(target.read_text())
        except OSError as exc:
            raise DatasetError(f"cannot read dataset: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise DatasetError(f"dataset is not valid JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise DatasetError("dataset must be a JSON object")
        return ClaimDataset.from_mapping(payload, source=str(target))
    return _stream_dataset(target, drop_id=drop_id, use_index=True)

