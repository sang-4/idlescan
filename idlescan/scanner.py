"""The scan itself: balances, claim status, and what it is all worth.

Read-only by construction - the only RPC methods used are ``eth_chainId``,
``eth_blockNumber``, ``eth_getCode``, ``eth_getBalance``, ``eth_call``,
``eth_gasPrice`` and ``eth_estimateGas``.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from decimal import Decimal
from typing import Any, Callable, Sequence

from .abi import AbiError, calldata, decode_one, normalise_address
from .cache import Cache
from .chains import Chain, ChainConfig, Token
from .merkle import Claim, ClaimDataset, parse_dataset, verify_proof
from .prices import PriceOracle, provider_id_for
from .registry import Airdrop
from .rpc import Rpc, RpcError, describe_error

META_TTL = 30 * 24 * 3600

UNCLAIMED = "unclaimed"
CLAIMED = "claimed"
NO_ALLOCATION = "no_allocation"
NO_SOURCE = "no_eligibility_source"
UNKNOWN = "unknown"
ERROR = "error"


def _parse_signature(signature: str) -> tuple[str, list[str]]:
    name, _, rest = signature.partition("(")
    inner = rest.rstrip(")").strip()
    types = [part.strip() for part in inner.split(",")] if inner else []
    return name.strip(), types


def _human(raw: int, decimals: int) -> Decimal:
    if decimals <= 0:
        return Decimal(raw)
    return Decimal(raw) / (Decimal(10) ** decimals)


@dataclass
class Holding:
    chain: str
    chain_id: int
    wallet: str
    symbol: str
    asset_address: str | None
    decimals: int
    raw: int
    stale: bool = False
    usd_price: float | None = None

    @property
    def amount(self) -> Decimal:
        return _human(self.raw, self.decimals)

    @property
    def usd_value(self) -> float | None:
        if self.usd_price is None:
            return None
        return float(self.amount) * self.usd_price

    @property
    def is_native(self) -> bool:
        return self.asset_address is None


@dataclass
class ClaimCheck:
    drop_id: str
    name: str
    chain: str
    chain_id: int
    wallet: str
    distributor: str
    status: str
    symbol: str = "?"
    token: str | None = None
    decimals: int = 18
    amount_raw: int = 0
    usd_price: float | None = None
    gas_native: float | None = None
    gas_usd: float | None = None
    native_symbol: str = "ETH"
    proof_verified: bool | None = None
    source: str | None = None
    note: str | None = None
    deadline: str | None = None
    expired: bool = False
    calldata: str | None = None

    @property
    def amount(self) -> Decimal:
        return _human(self.amount_raw, self.decimals)

    @property
    def usd_value(self) -> float | None:
        """Value of the allocation; ``None`` when no price is known."""
        if self.usd_price is None:
            return None
        return float(self.amount) * self.usd_price

    @property
    def net_usd(self) -> float | None:
        if self.status != UNCLAIMED:
            return None
        value = self.usd_value
        if value is None:
            return None
        return value - (self.gas_usd or 0.0)

    @property
    def claimable(self) -> bool:
        return self.status == UNCLAIMED and not self.expired

    @property
    def label(self) -> str:
        return f"{self.name} ({self.chain})"


@dataclass
class ScanReport:
    wallets: list[str]
    chains: list[str]
    holdings: list[Holding] = field(default_factory=list)
    claims: list[ClaimCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    block_numbers: dict[str, int] = field(default_factory=dict)
    generated_at: str = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    )
    price_error: str | None = None

    @property
    def claim_value(self) -> float | None:
        values = [
            c.usd_value
            for c in self.claims
            if c.status == UNCLAIMED and not c.expired and c.usd_value is not None
        ]
        return sum(values) if values else None

    @property
    def idle_value(self) -> float | None:
        values = [h.usd_value for h in self.holdings if h.usd_value is not None]
        return sum(values) if values else None

    @property
    def total_value(self) -> float | None:
        parts = [v for v in (self.idle_value, self.claim_value) if v is not None]
        return sum(parts) if parts else None


class Scanner:
    def __init__(
        self,
        chains: ChainConfig,
        tokens: dict[str, list[Token]],
        drops: Sequence[Airdrop],
        *,
        cache: Cache | None = None,
        prices: PriceOracle | None = None,
        rpc_factory: Callable[[Chain], Rpc] | None = None,
        verify_tokens: bool = True,
        estimate_gas: bool = True,
        verbose: Callable[[str], None] | None = None,
        http_fetch: Callable[[str], dict] | None = None,
    ):
        self.chains = chains
        self.tokens = tokens
        self.drops = list(drops)
        self.cache = cache if cache is not None else Cache()
        self.prices = prices or PriceOracle(cache=self.cache)
        self.rpc_factory = rpc_factory or (lambda chain: Rpc(list(chain.rpcs)))
        self.verify_tokens = verify_tokens
        self.estimate_gas = estimate_gas
        self.verbose = verbose or (lambda message: None)
        self.http_fetch = http_fetch or _fetch_json
        self._rpcs: dict[str, Rpc] = {}
        self._datasets: dict[str, ClaimDataset | None] = {}
        self._leaf_modes: dict[str, tuple[str, bool] | None] = {}
        self._symbols: dict[str, str] = {}

    # ------------------------------------------------------------------ setup
    def _rpc(self, chain: Chain, report: ScanReport) -> Rpc:
        if chain.key in self._rpcs:
            return self._rpcs[chain.key]
        rpc = self.rpc_factory(chain)
        try:
            actual = int(rpc.request("eth_chainId"), 16)
        except (RpcError, TypeError, ValueError) as exc:
            report.warnings.append(f"{chain.name}: unreachable ({describe_error(exc)})")
            raise
        if actual != chain.chain_id:
            report.warnings.append(
                f"{chain.name}: RPC reports chain id {actual}, expected {chain.chain_id} - skipped"
            )
            raise RpcError(f"chain id mismatch for {chain.name}")
        self._rpcs[chain.key] = rpc
        return rpc

    # ------------------------------------------------------------------- scan
    def scan(self, chain_keys: Sequence[str] | None, wallets: Sequence[str]) -> ScanReport:
        normalised = [normalise_address(w) for w in wallets]
        chains = self.chains.resolve(chain_keys)
        report = ScanReport(wallets=normalised, chains=[c.key for c in chains])

        for chain in chains:
            self.verbose(f"scanning {chain.name}...")
            try:
                rpc = self._rpc(chain, report)
            except RpcError:
                continue
            try:
                report.block_numbers[chain.key] = rpc.block_number()
            except RpcError as exc:
                report.warnings.append(f"{chain.name}: block number unavailable ({describe_error(exc)})")

            self._scan_balances(chain, rpc, normalised, report)
            for drop in self.drops:
                if drop.chain != chain.key or not drop.enabled:
                    continue
                self._check_drop(chain, rpc, drop, normalised, report)

        self._apply_prices(report)
        report.price_error = self.prices.error
        self.cache.flush()
        return report

    # -------------------------------------------------------------- balances
    def _scan_balances(
        self, chain: Chain, rpc: Rpc, wallets: Sequence[str], report: ScanReport
    ) -> None:
        calls: list[tuple[str, Sequence[Any]]] = [
            ("eth_getBalance", [wallet, "latest"]) for wallet in wallets
        ]
        tokens = self._resolve_tokens(chain, rpc, report) if self.tokens.get(chain.key) else []
        token_calls: list[tuple[Token, str, str]] = []
        for token in tokens:
            for wallet in wallets:
                data = calldata("balanceOf(address)", ["address"], [wallet])
                token_calls.append((token, wallet, data))
                calls.append(("eth_call", [{"to": token.address, "data": data}, "latest"]))

        results = rpc.batch(calls)
        for index, wallet in enumerate(wallets):
            value = results[index]
            if isinstance(value, Exception) or not isinstance(value, str):
                report.warnings.append(
                    f"{chain.name}: balance lookup failed for {wallet} ({describe_error(value)})"
                )
                continue
            if int(value, 16) > 0:
                report.holdings.append(
                    Holding(
                        chain=chain.key,
                        chain_id=chain.chain_id,
                        wallet=wallet,
                        symbol=chain.native.symbol,
                        asset_address=None,
                        decimals=chain.native.decimals,
                        raw=int(value, 16),
                    )
                )

        offset = len(wallets)
        for position, (token, wallet, _) in enumerate(token_calls):
            value = results[offset + position]
            if isinstance(value, Exception) or not isinstance(value, str) or len(value) < 3:
                continue
            raw = int(value, 16) if value != "0x" else 0
            if raw == 0:
                continue
            report.holdings.append(
                Holding(
                    chain=chain.key,
                    chain_id=chain.chain_id,
                    wallet=wallet,
                    symbol=token.symbol,
                    asset_address=token.address,
                    decimals=token.decimals,
                    raw=raw,
                )
            )

    def _resolve_tokens(
        self, chain: Chain, rpc: Rpc, report: ScanReport
    ) -> list[Token]:
        configured = self.tokens.get(chain.key, [])
        if not configured:
            return []
        resolved: list[Token] = []
        missing: list[Token] = []
        for token in configured:
            cached = self.cache.get(f"token:{chain.key}:{token.address}", max_age=META_TTL)
            if isinstance(cached, dict):
                if not cached.get("ok"):
                    continue
                resolved.append(
                    Token(
                        chain=chain.key,
                        address=token.address,
                        symbol=token.symbol,
                        decimals=int(cached.get("decimals", token.decimals)),
                    )
                )
            else:
                missing.append(token)

        if missing and self.verify_tokens:
            calls: list[tuple[str, Sequence[Any]]] = []
            for token in missing:
                calls.append(("eth_getCode", [token.address, "latest"]))
                calls.append(("eth_call", [{"to": token.address, "data": calldata("decimals()")}, "latest"]))
                calls.append(
                    ("eth_call", [{"to": token.address, "data": calldata("symbol()")}, "latest"])
                )
            results = rpc.batch(calls)
            for index, token in enumerate(missing):
                code, dec_raw, sym_raw = results[index * 3 : index * 3 + 3]
                ok = isinstance(code, str) and len(code) > 2
                decimals = token.decimals
                if ok and isinstance(dec_raw, str) and len(dec_raw) >= 66:
                    try:
                        decimals = int(dec_raw[2:66], 16)
                    except ValueError:
                        ok = False
                if not ok:
                    self.cache.set(f"token:{chain.key}:{token.address}", {"ok": False})
                    report.warnings.append(
                        f"{chain.name}: {token.symbol} {token.address} is not a usable ERC-20 here - skipped"
                    )
                    continue
                onchain_symbol = None
                if isinstance(sym_raw, str) and len(sym_raw) > 2:
                    onchain_symbol = decode_one("string", sym_raw)
                if decimals != token.decimals:
                    report.warnings.append(
                        f"{chain.name}: {token.symbol} reports {decimals} decimals, config said "
                        f"{token.decimals}; using on-chain value"
                    )
                if onchain_symbol and onchain_symbol != token.symbol:
                    # Labels like USDC.e vs USDC are intentional, so keep this quiet
                    # unless the user asks for detail.
                    self.verbose(
                        f"  {chain.name}: {token.address} on-chain symbol {onchain_symbol!r}, "
                        f"registry label {token.symbol!r}"
                    )
                self.cache.set(
                    f"token:{chain.key}:{token.address}",
                    {"ok": True, "decimals": decimals, "symbol": onchain_symbol},
                )
                resolved.append(
                    Token(
                        chain=chain.key,
                        address=token.address,
                        symbol=token.symbol,
                        decimals=decimals,
                    )
                )
        else:
            resolved.extend(missing)
        return resolved

    # ----------------------------------------------------------------- claims
    def _check_drop(
        self,
        chain: Chain,
        rpc: Rpc,
        drop: Airdrop,
        wallets: Sequence[str],
        report: ScanReport,
    ) -> None:
        self._symbols[drop.id] = self._resolve_drop_symbol(chain, rpc, drop)
        if drop.eligibility is None:
            for wallet in wallets:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, NO_SOURCE, note="no eligibility source configured")
                )
            return

        if drop.eligibility.is_merkle:
            self._check_merkle(chain, rpc, drop, wallets, report)
        else:
            self._check_allocation_view(chain, rpc, drop, wallets, report)

    def _resolve_drop_symbol(self, chain: Chain, rpc: Rpc, drop: Airdrop) -> str:
        if not drop.token:
            return drop.name
        key = f"token-symbol:{chain.key}:{drop.token}"
        cached = self.cache.get(key, max_age=META_TTL)
        if isinstance(cached, str) and cached:
            return cached
        symbol = None
        try:
            raw = rpc.eth_call(drop.token, calldata("symbol()"))
            if isinstance(raw, str) and len(raw) >= 66:
                symbol = decode_one("string", raw)
        except (RpcError, AbiError):
            symbol = None
        symbol = symbol or drop.name
        self.cache.set(key, symbol)
        return symbol

    def _claim_row(self, drop: Airdrop, chain: Chain, wallet: str, status: str, **kwargs: Any) -> ClaimCheck:
        return ClaimCheck(
            drop_id=drop.id,
            name=drop.name,
            chain=chain.key,
            chain_id=chain.chain_id,
            wallet=wallet,
            distributor=drop.distributor,
            status=status,
            symbol=kwargs.pop("symbol", "?"),
            token=drop.token,
            decimals=kwargs.pop("decimals", drop.token_decimals),
            native_symbol=chain.native.symbol,
            deadline=drop.claim_deadline,
            expired=_is_expired(drop.claim_deadline),
            **kwargs,
        )

    # ------------------------------------------------------------ merkle path
    def _load_dataset(self, drop: Airdrop, report: ScanReport) -> ClaimDataset | None:
        if drop.id in self._datasets:
            return self._datasets[drop.id]
        dataset: ClaimDataset | None = None
        eligibility = drop.eligibility
        assert eligibility is not None
        if eligibility.type == "merkle_dataset":
            path = eligibility.dataset or ""
            try:
                payload = json.loads(Path(path).read_text())
                dataset = parse_dataset(payload, source=path)
                self.verbose(f"  {drop.name}: loaded {len(dataset)} claims from {path}")
            except FileNotFoundError:
                report.warnings.append(
                    f"{drop.name}: claims dataset not found ({Path(path).name}) - drop cannot be checked "
                    f"(add the published distribution file to the dataset directory)"
                )
            except (OSError, ValueError) as exc:
                report.warnings.append(
                    f"{drop.name}: claims dataset {Path(path).name} could not be read "
                    f"({exc.__class__.__name__})"
                )
        self._datasets[drop.id] = dataset
        return dataset

    def _check_merkle(
        self,
        chain: Chain,
        rpc: Rpc,
        drop: Airdrop,
        wallets: Sequence[str],
        report: ScanReport,
    ) -> None:
        eligibility = drop.eligibility
        assert eligibility is not None
        root: bytes | None = None
        if drop.root_method:
            try:
                raw = rpc.eth_call(drop.distributor, calldata(drop.root_method))
                if isinstance(raw, str) and len(raw) >= 66:
                    root = bytes.fromhex(raw[2:66])
            except RpcError as exc:
                report.warnings.append(
                    f"{drop.name}: could not read {drop.root_method} ({describe_error(exc)})"
                )

        if eligibility.type == "dataset_url":
            self._check_merkle_remote(chain, rpc, drop, wallets, report, root)
            return

        dataset = self._load_dataset(drop, report)
        if dataset is None:
            for wallet in wallets:
                report.claims.append(
                    self._claim_row(
                        drop, chain, wallet, NO_SOURCE,
                        note="claims dataset missing; see warning",
                        symbol=self._symbols.get(drop.id, drop.name),
                    )
                )
            return

        if root and dataset.root and dataset.root != root:
            for wallet in wallets:
                report.claims.append(
                    self._claim_row(
                        drop, chain, wallet, ERROR,
                        note="dataset merkle root does not match the contract on chain",
                        symbol=self._symbols.get(drop.id, drop.name),
                    )
                )
            report.warnings.append(
                f"{drop.name}: dataset root {dataset.root.hex()} != on-chain root {root.hex()}"
            )
            return

        encoding, double_hash = self._resolve_leaf_mode(drop, dataset, root, report)

        indexes = [dataset.get(wallet) for wallet in wallets]
        claimed_flags = self._read_claimed(rpc, drop, [c.index if c else None for c in indexes])

        for wallet, claim, claimed in zip(wallets, indexes, claimed_flags):
            if claim is None:
                report.claims.append(
                    self._claim_row(
                        drop, chain, wallet, NO_ALLOCATION,
                        note=f"address absent from {len(dataset)}-claim dataset",
                        symbol=self._symbols.get(drop.id, drop.name),
                        source=dataset.source,
                    )
                )
                continue

            proof_ok: bool | None = None
            if root is not None:
                proof_ok = verify_proof(
                    claim.index,
                    wallet,
                    claim.amount,
                    claim.proof,
                    root,
                    encoding=encoding,
                    double_hash=double_hash,
                    sort_pairs=dataset.sort_pairs,
                )
            if proof_ok is False:
                report.claims.append(
                    self._claim_row(
                        drop, chain, wallet, ERROR,
                        amount_raw=claim.amount,
                        note="proof in dataset does not verify against the on-chain root",
                        symbol=self._symbols.get(drop.id, drop.name),
                        source=dataset.source,
                        proof_verified=False,
                    )
                )
                continue

            status = CLAIMED if claimed is True else (UNCLAIMED if claimed is False else UNKNOWN)
            note = None
            if claimed is None:
                note = "claim state unavailable from RPC"
            row = self._claim_row(
                drop, chain, wallet, status,
                amount_raw=claim.amount,
                symbol=self._symbols.get(drop.id, drop.name),
                source=dataset.source,
                proof_verified=proof_ok,
                note=note,
            )
            if status == UNCLAIMED:
                row.calldata = self._claim_calldata(drop, wallet, claim)
                self._attach_gas(chain, rpc, row, wallet, report)
            report.claims.append(row)

    def _check_merkle_remote(
        self,
        chain: Chain,
        rpc: Rpc,
        drop: Airdrop,
        wallets: Sequence[str],
        report: ScanReport,
        root: bytes | None,
    ) -> None:
        eligibility = drop.eligibility
        assert eligibility is not None and eligibility.url
        for wallet in wallets:
            url = eligibility.url.format(address=wallet)
            try:
                payload = self.http_fetch(url)
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 400):
                    report.claims.append(
                        self._claim_row(drop, chain, wallet, NO_ALLOCATION, note=f"no claim record (HTTP {exc.code})", symbol=self._symbols.get(drop.id, drop.name), source=url)
                    )
                else:
                    report.claims.append(
                        self._claim_row(drop, chain, wallet, ERROR, note=f"claim API HTTP {exc.code}", symbol=self._symbols.get(drop.id, drop.name), source=url)
                    )
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, ERROR, note=f"claim API failed: {describe_error(exc)}", symbol=self._symbols.get(drop.id, drop.name), source=url)
                )
                continue

            if not isinstance(payload, dict) or not (
                payload.get("claims") or payload.get("proof") or payload.get("amount")
            ):
                report.claims.append(
                    self._claim_row(drop, chain, wallet, NO_ALLOCATION, note="claim API returned no allocation", symbol=self._symbols.get(drop.id, drop.name), source=url)
                )
                continue

            entry = payload.get("claims", payload)
            if not isinstance(entry, dict) or wallet.lower() not in {k.lower() for k in entry}:
                entry = {wallet: entry} if "index" in entry or "amount" in entry else entry
            try:
                dataset = parse_dataset({"claims": entry, "merkleRoot": payload.get("merkleRoot")}, source=url)
            except ValueError as exc:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, ERROR, note=f"bad claim payload: {exc}", symbol=self._symbols.get(drop.id, drop.name), source=url)
                )
                continue
            claim = dataset.get(wallet)
            if claim is None:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, NO_ALLOCATION, note="claim API has no entry for this address", symbol=self._symbols.get(drop.id, drop.name), source=url)
                )
                continue

            encoding, double_hash = self._resolve_leaf_mode(drop, dataset, root, report)
            proof_ok: bool | None = None
            if root is not None:
                proof_ok = verify_proof(
                    claim.index, wallet, claim.amount, claim.proof, root,
                    encoding=encoding, double_hash=double_hash, sort_pairs=dataset.sort_pairs,
                )
            if proof_ok is False:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, ERROR, amount_raw=claim.amount, note="API proof does not verify against on-chain root (do not trust this API)", symbol=self._symbols.get(drop.id, drop.name), source=url, proof_verified=False)
                )
                continue

            claimed_flags = self._read_claimed(rpc, drop, [claim.index])
            claimed = claimed_flags[0]
            status = CLAIMED if claimed is True else (UNCLAIMED if claimed is False else UNKNOWN)
            row = self._claim_row(
                drop, chain, wallet, status,
                amount_raw=claim.amount, symbol=self._symbols.get(drop.id, drop.name), source=url, proof_verified=proof_ok,
                note=None if claimed is not None else "claim state unavailable from RPC",
            )
            if status == UNCLAIMED:
                row.calldata = self._claim_calldata(drop, wallet, claim)
                self._attach_gas(chain, rpc, row, wallet, report)
            report.claims.append(row)

    def _resolve_leaf_mode(
        self,
        drop: Airdrop,
        dataset: ClaimDataset,
        root: bytes | None,
        report: ScanReport,
    ) -> tuple[str, bool]:
        if drop.id in self._leaf_modes and self._leaf_modes[drop.id] is not None:
            return self._leaf_modes[drop.id]  # type: ignore[return-value]

        eligibility = drop.eligibility
        assert eligibility is not None
        chosen: tuple[str, bool] | None = None
        if eligibility.leaf_encoding != "auto":
            chosen = (eligibility.leaf_encoding, eligibility.double_hash)
        elif root is not None:
            samples = [(addr, claim) for addr, claim in dataset.claims.items() if claim.proof][:3]
            for encoding, double in (("packed", False), ("abi", False), ("packed", True), ("abi", True)):
                if samples and all(
                    verify_proof(
                        claim.index, addr, claim.amount, claim.proof, root,
                        encoding=encoding, double_hash=double, sort_pairs=dataset.sort_pairs,
                    )
                    for addr, claim in samples
                ):
                    chosen = (encoding, double)
                    self.verbose(f"  {drop.name}: leaf encoding detected as {encoding}"
                                 f"{' (double-hashed)' if double else ''}")
                    break
            if chosen is None and samples:
                report.warnings.append(
                    f"{drop.name}: no known leaf encoding reproduces the on-chain root - "
                    f"dataset and contract may not belong together"
                )
        if chosen is None:
            chosen = ("packed", False) if eligibility.leaf_encoding == "auto" else (
                eligibility.leaf_encoding,
                eligibility.double_hash,
            )
            if eligibility.leaf_encoding == "auto":
                report.warnings.append(
                    f"{drop.name}: leaf encoding could not be verified; assuming "
                    f"keccak256(abi.encodePacked(index, account, amount))"
                )
        self._leaf_modes[drop.id] = chosen
        return chosen

    def _read_claimed(
        self, rpc: Rpc, drop: Airdrop, indexes: Sequence[int | None]
    ) -> list[bool | None]:
        calls: list[tuple[str, Sequence[Any]]] = []
        slots: list[int] = []
        for position, index in enumerate(indexes):
            if index is None:
                continue
            try:
                data = calldata(drop.claimed_method, ["uint256"], [index])
            except (AbiError, ValueError):
                continue
            calls.append(("eth_call", [{"to": drop.distributor, "data": data}, "latest"]))
            slots.append(position)
        results: list[bool | None] = [None] * len(indexes)
        if not calls:
            return results
        for position, result in zip(slots, rpc.batch(calls)):
            if isinstance(result, str) and len(result) >= 66:
                results[position] = int(result[2:66], 16) != 0
        return results

    def _claim_calldata(self, drop: Airdrop, wallet: str, claim: Claim) -> str | None:
        try:
            _, types = _parse_signature(drop.claim_method)
            values: list[Any] = []
            uint_seen = 0
            for type_name in types:
                if type_name == "address":
                    values.append(wallet)
                elif type_name == "bytes32[]":
                    values.append(["0x" + item.hex() for item in claim.proof])
                elif type_name.startswith("uint"):
                    values.append(claim.index if uint_seen == 0 else claim.amount)
                    uint_seen += 1
                else:
                    return None
            return calldata(drop.claim_method, types, values)
        except (AbiError, ValueError):
            return None

    # -------------------------------------------------------- allocation view
    def _check_allocation_view(
        self,
        chain: Chain,
        rpc: Rpc,
        drop: Airdrop,
        wallets: Sequence[str],
        report: ScanReport,
    ) -> None:
        eligibility = drop.eligibility
        assert eligibility is not None
        try:
            alloc_calls = [
                ("eth_call", [{"to": drop.distributor, "data": calldata(eligibility.method, ["address"], [wallet])}, "latest"])
                for wallet in wallets
            ]
        except (AbiError, ValueError) as exc:
            report.warnings.append(f"{drop.name}: bad eligibility method {eligibility.method!r}: {exc}")
            return
        allocations = rpc.batch(alloc_calls)

        eligible = [
            (wallet, _decode_uint(result))
            for wallet, result in zip(wallets, allocations)
        ]
        try:
            claimed_calls = [
                ("eth_call", [{"to": drop.distributor, "data": calldata(drop.claimed_method, ["address"], [wallet])}, "latest"])
                for wallet, amount in eligible
                if amount
            ]
        except (AbiError, ValueError) as exc:
            report.warnings.append(f"{drop.name}: bad claimed method {drop.claimed_method!r}: {exc}")
            return
        claimed_values = rpc.batch(claimed_calls) if claimed_calls else []

        claimed_by_wallet: dict[str, bool | None] = {}
        for wallet, value in zip([w for w, a in eligible if a], claimed_values):
            claimed_by_wallet[wallet] = _decode_bool(value)

        for wallet, amount in eligible:
            if not amount:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, NO_ALLOCATION, note="allocation is zero", symbol=self._symbols.get(drop.id, drop.name), source=eligibility.method)
                )
                continue
            claimed = claimed_by_wallet.get(wallet)
            status = CLAIMED if claimed is True else (UNCLAIMED if claimed is False else UNKNOWN)
            row = self._claim_row(
                drop, chain, wallet, status,
                amount_raw=amount, decimals=eligibility.decimals,
                symbol=self._symbols.get(drop.id, drop.name), source=eligibility.method,
                note=None if claimed is not None else "claim state unavailable from RPC",
            )
            if status == UNCLAIMED:
                self._attach_gas(chain, rpc, row, wallet, report)
            report.claims.append(row)

    # -------------------------------------------------------------------- gas
    def _attach_gas(
        self, chain: Chain, rpc: Rpc, row: ClaimCheck, wallet: str, report: ScanReport
    ) -> None:
        if not self.estimate_gas or not row.calldata:
            return
        try:
            estimate = rpc.request(
                "eth_estimateGas", {"from": wallet, "to": row.distributor, "data": row.calldata}
            )
            gas_units = int(estimate, 16) if isinstance(estimate, str) else int(estimate)
            price = int(rpc.request("eth_gasPrice"), 16)
        except (RpcError, TypeError, ValueError):
            return
        row.gas_native = gas_units * price / 1e18

    # ----------------------------------------------------------------- prices
    def _apply_prices(self, report: ScanReport) -> None:
        if not self.prices.enabled:
            return
        chain_index = {chain.key: chain for chain in self.chains.chains.values()}
        asset_ids: dict[str, str] = {}
        for holding in report.holdings:
            chain = chain_index.get(holding.chain)
            if chain is None:
                continue
            provider = provider_id_for(chain, holding.asset_address)
            if provider:
                asset_ids[f"{holding.chain}|{holding.asset_address or 'native'}"] = provider
        for row in report.claims:
            chain = chain_index.get(row.chain)
            if chain is None or not row.token:
                continue
            provider = provider_id_for(chain, row.token)
            if provider:
                asset_ids[f"{row.chain}|{row.token}"] = provider

        quotes = self.prices.quote(asset_ids)

        for holding in report.holdings:
            holding.usd_price = quotes.get(f"{holding.chain}|{holding.asset_address or 'native'}")
        for row in report.claims:
            if not row.token:
                continue
            row.usd_price = quotes.get(f"{row.chain}|{row.token}")
            if row.gas_native is None:
                continue
            native_price = quotes.get(f"{row.chain}|native")
            if native_price is not None:
                row.gas_usd = row.gas_native * native_price


def _decode_uint(value: Any) -> int:
    if isinstance(value, str) and len(value) >= 66:
        try:
            return int(value[2:66], 16)
        except ValueError:
            return 0
    return 0


def _decode_bool(value: Any) -> bool | None:
    if isinstance(value, str) and len(value) >= 66:
        try:
            return int(value[2:66], 16) != 0
        except ValueError:
            return None
    return None





def _is_expired(deadline: str | None) -> bool:
    if not deadline:
        return False
    try:
        parsed = dt.datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed < dt.datetime.now(dt.timezone.utc)


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"accept": "application/json", "user-agent": "idlescan/1.0"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())
