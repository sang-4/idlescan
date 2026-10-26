"""Chain and token configuration.

Configuration lives inside the package (``idlescan/data/*.json``) so the tool
works from a clone with no setup, and can be overridden with explicit paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .abi import normalise_address

DATA_DIR = Path(__file__).resolve().parent / "data"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NativeAsset:
    symbol: str
    decimals: int
    coingecko: str | None = None


FEE_MODELS = ("eip1559", "legacy")
L1_FEE_STRATEGIES = ("none", "included", "op_stack", "scroll", "unknown")


@dataclass(frozen=True)
class Chain:
    key: str
    chain_id: int
    name: str
    native: NativeAsset
    rpcs: tuple[str, ...]
    explorer: str | None = None
    coingecko_platform: str | None = None
    defillama: str | None = None
    fee_model: str = "eip1559"
    l1_fee: str = "none"
    l1_fee_oracle: str | None = None
    stale_block_seconds: int = 3600

    @property
    def address_url(self) -> str | None:
        return f"{self.explorer}/address/" if self.explorer else None


@dataclass(frozen=True)
class Token:
    chain: str
    address: str
    symbol: str
    decimals: int


@dataclass
class ChainConfig:
    chains: dict[str, Chain] = field(default_factory=dict)

    def get(self, key: str) -> Chain:
        try:
            return self.chains[key.lower()]
        except KeyError:
            raise ConfigError(
                f"unknown chain {key!r}; known: {', '.join(sorted(self.chains))}"
            ) from None

    def resolve(self, keys: Iterable[str] | None) -> list[Chain]:
        if not keys:
            return [self.chains[key] for key in sorted(self.chains)]
        out: list[Chain] = []
        for raw in keys:
            key = raw.strip().lower()
            if not key:
                continue
            if key in ("all", "*"):
                return [self.chains[name] for name in sorted(self.chains)]
            chain = self.get(key)
            if chain not in out:
                out.append(chain)
        return out


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON ({exc})") from exc


def load_chains(path: Path | str | None = None) -> ChainConfig:
    data = _read_json(Path(path) if path else DATA_DIR / "chains.json")
    chains: dict[str, Chain] = {}
    for raw in data.get("chains", []):
        try:
            native = raw["native"]
            chain = Chain(
                key=str(raw["key"]).lower(),
                chain_id=int(raw["chain_id"]),
                name=raw.get("name") or raw["key"],
                native=NativeAsset(
                    symbol=native["symbol"],
                    decimals=int(native.get("decimals", 18)),
                    coingecko=native.get("coingecko"),
                ),
                rpcs=tuple(raw["rpcs"]),
                explorer=raw.get("explorer"),
                coingecko_platform=raw.get("coingecko_platform"),
                defillama=raw.get("defillama"),
                fee_model=str(raw.get("fee_model") or "eip1559"),
                l1_fee=str(raw.get("l1_fee") or "none"),
                l1_fee_oracle=raw.get("l1_fee_oracle"),
                stale_block_seconds=int(raw.get("stale_block_seconds", 3600)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"bad chain entry {raw!r}: {exc}") from exc
        if chain.fee_model not in FEE_MODELS:
            raise ConfigError(
                f"chain {chain.key} has unknown fee_model {chain.fee_model!r}; "
                f"expected one of {', '.join(FEE_MODELS)}"
            )
        if chain.l1_fee not in L1_FEE_STRATEGIES:
            raise ConfigError(
                f"chain {chain.key} has unknown l1_fee {chain.l1_fee!r}; "
                f"expected one of {', '.join(L1_FEE_STRATEGIES)}"
            )
        if not chain.rpcs:
            raise ConfigError(f"chain {chain.key} has no RPC endpoints")
        chains[chain.key] = chain
    if not chains:
        raise ConfigError("no chains configured")
    return ChainConfig(chains=chains)


def load_tokens(
    config: ChainConfig, path: Path | str | None = None
) -> dict[str, list[Token]]:
    data = _read_json(Path(path) if path else DATA_DIR / "tokens.json")
    out: dict[str, list[Token]] = {}
    for chain_key, entries in (data.get("chains") or {}).items():
        key = str(chain_key).lower()
        config.get(key)  # raises for typos
        tokens: list[Token] = []
        for raw in entries or []:
            try:
                tokens.append(
                    Token(
                        chain=key,
                        address=normalise_address(raw["address"]),
                        symbol=str(raw.get("symbol") or "?"),
                        decimals=int(raw["decimals"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(f"bad token for {key}: {raw!r}: {exc}") from exc
        out[key] = tokens
    return out
