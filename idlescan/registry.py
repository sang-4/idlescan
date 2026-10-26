"""Airdrop registry: which contracts to probe and how to read them.

There is no universal "unclaimed airdrops" API.  A distributor contract can tell
you whether a *specific index* was claimed, but it cannot tell you whether an
address was ever allocated anything - that requires an eligibility source
(a published merkle dataset, a claim API, or an on-chain allocation view).
Each registry entry therefore declares both halves:

  eligibility  -> is this address owed something, and how much
  claimed_check-> has that specific allocation been taken already
  root_check   -> optional, lets us verify third-party proofs on chain
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .abi import normalise_address
from .chains import ChainConfig, ConfigError

ELIGIBILITY_TYPES = ("merkle_dataset", "dataset_url", "allocation_view")
LEAF_ENCODINGS = ("auto", "abi", "packed")


@dataclass
class Eligibility:
    type: str
    dataset: str | None = None
    url: str | None = None
    method: str = "allocation(address)"
    returns: str = "uint256"
    decimals: int = 18
    leaf_encoding: str = "auto"
    double_hash: bool = False
    sort_pairs: bool = True

    @property
    def is_merkle(self) -> bool:
        return self.type in ("merkle_dataset", "dataset_url")


@dataclass
class Airdrop:
    id: str
    name: str
    chain: str
    distributor: str
    token: str | None
    token_decimals: int = 18
    eligibility: Eligibility | None = None
    claim_method: str = "claim(uint256,address,uint256,bytes32[])"
    claimed_method: str = "isClaimed(uint256)"
    claimed_arg: str = "index"
    root_method: str | None = "merkleRoot()"
    claim_deadline: str | None = None
    notes: str | None = None
    links: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    verified_on: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name} [{self.id}]"


def _require_address(value: Any, field_name: str) -> str:
    try:
        return normalise_address(str(value))
    except ValueError as exc:
        raise ConfigError(f"{field_name}: {exc}") from None


def _parse_eligibility(raw: dict[str, Any], where: str) -> Eligibility:
    kind = str(raw.get("type", "")).strip()
    if kind not in ELIGIBILITY_TYPES:
        raise ConfigError(
            f"{where}: eligibility.type must be one of {', '.join(ELIGIBILITY_TYPES)}"
        )
    encoding = str(raw.get("leaf_encoding", raw.get("leaf", "auto"))).lower()
    if encoding not in LEAF_ENCODINGS:
        raise ConfigError(f"{where}: leaf_encoding must be auto, abi or packed")
    eligibility = Eligibility(
        type=kind,
        dataset=raw.get("dataset"),
        url=raw.get("url"),
        method=str(raw.get("method", "allocation(address)")),
        returns=str(raw.get("returns", "uint256")),
        decimals=int(raw.get("decimals", 18)),
        leaf_encoding=encoding,
        double_hash=bool(raw.get("double_hash", False)),
        sort_pairs=bool(raw.get("sort_pairs", True)),
    )
    if kind == "merkle_dataset" and not eligibility.dataset:
        raise ConfigError(f"{where}: merkle_dataset requires 'dataset'")
    if kind == "dataset_url" and not (eligibility.url and "{address}" in eligibility.url):
        raise ConfigError(
            f"{where}: dataset_url requires 'url' containing the {{address}} placeholder"
        )
    return eligibility


def parse_registry(data: dict[str, Any], chains: ChainConfig) -> list[Airdrop]:
    drops: list[Airdrop] = []
    seen: set[str] = set()
    for index, raw in enumerate(data.get("airdrops") or []):
        where = f"airdrops[{index}]"
        try:
            drop_id = str(raw["id"]).strip()
            if not drop_id:
                raise ConfigError(f"{where}: empty id")
            if drop_id in seen:
                raise ConfigError(f"{where}: duplicate id {drop_id!r}")
            seen.add(drop_id)
            chain_key = str(raw["chain"]).lower()
            chains.get(chain_key)  # validates the chain key early
            drop = Airdrop(
                id=drop_id,
                name=str(raw.get("name") or drop_id),
                chain=chain_key,
                distributor=_require_address(raw["distributor"], f"{where}.distributor"),
                token=_require_address(raw["token"], f"{where}.token") if raw.get("token") else None,
                token_decimals=int(raw.get("token_decimals", 18)),
                claim_method=str(raw.get("claim_method") or "claim(uint256,address,uint256,bytes32[])"),
                claim_deadline=raw.get("claim_deadline"),
                notes=raw.get("notes"),
                links=dict(raw.get("links") or {}),
                enabled=bool(raw.get("enabled", True)),
                verified_on=raw.get("verified_on"),
            )
            if raw.get("eligibility"):
                drop.eligibility = _parse_eligibility(raw["eligibility"], where)
                if drop.eligibility.is_merkle:
                    drop.claimed_method = str(raw.get("claimed_method") or "isClaimed(uint256)")
                    drop.claimed_arg = "index"
                else:
                    drop.claimed_method = str(raw.get("claimed_method") or "claimed(address)")
                    drop.claimed_arg = "address"
            drop.root_method = raw.get("root_method", "merkleRoot()")
            if drop.root_method == "":
                drop.root_method = None
            # Distributor code presence is verified by `idlescan verify` / `scan`.
        except (KeyError, ConfigError, TypeError, ValueError) as exc:
            raise ConfigError(f"{where}: {exc}") from exc
        drops.append(drop)
    return drops


def load_registry(path: Path | str | None, chains: ChainConfig) -> list[Airdrop]:
    from .chains import DATA_DIR

    target = Path(path) if path else DATA_DIR / "airdrops.json"
    try:
        data = json.loads(target.read_text())
    except FileNotFoundError:
        raise ConfigError(f"registry not found: {target}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{target}: invalid JSON ({exc})") from exc
    drops = parse_registry(data, chains)
    for drop in drops:
        if drop.eligibility and drop.eligibility.dataset:
            candidate = Path(drop.eligibility.dataset)
            if not candidate.is_absolute():
                drop.eligibility.dataset = str(
                    (target.parent / candidate).resolve()
                )
    return drops
