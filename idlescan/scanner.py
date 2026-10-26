"""The scan itself: balances, claim status, and what it is all worth.

Read-only by construction - the only RPC methods used are ``eth_chainId``,
``eth_blockNumber``, ``eth_getCode``, ``eth_getBalance``, ``eth_call``,
``eth_feeHistory``, ``eth_maxPriorityFeePerGas``, ``eth_gasPrice`` and
``eth_estimateGas``.  Nothing here can sign or broadcast.
"""

from __future__ import annotations

import datetime as dt
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from decimal import Decimal
from typing import Any, Callable, Sequence

from .abi import AbiError, calldata, decode_one, normalise_address
from .cache import Cache
from .chains import Chain, ChainConfig, Token
from .dataset import (
    MAX_DATASET_BYTES,
    DatasetError,
    dataset_provenance,
    is_stale,
    parse_dataset_file,
)
from .merkle import Claim, ClaimDataset, parse_dataset, verify_proof
from .gas import PARTIAL, UNAVAILABLE, buffered_wei, estimate_claim, gas_cost_native
from .prices import PriceOracle, provider_id_for
from .redaction import redact_url
from .safe_http import FetchError, safe_fetch_json
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
    # Raw (unbuffered) gas cost.  ``gas_native``/``gas_usd`` keep their v1.1
    # meaning so old exports and API consumers keep working.
    gas_native: float | None = None
    gas_usd: float | None = None
    gas_cost_native_buffered: float | None = None
    gas_cost_usd_buffered: float | None = None
    gas_units: int | None = None
    gas_limit: int | None = None
    fee_per_gas: int | None = None
    max_fee_per_gas: int | None = None
    max_priority_fee_per_gas: int | None = None
    fee_model: str | None = None
    fee_source: str | None = None
    l1_fee_wei: int | None = None
    gas_estimate_quality: str = UNAVAILABLE
    gas_note: str | None = None
    gas_buffer_pct: int = 20
    native_symbol: str = "ETH"
    proof_verified: bool | None = None
    root: str | None = None
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
        """Value after the *buffered* gas cost, or ``None`` when not knowable."""
        if self.status != UNCLAIMED or self.expired:
            return None
        value = self.usd_value
        if value is None or self.gas_estimate_quality == UNAVAILABLE:
            return None
        if self.gas_cost_usd_buffered is None:
            return None
        return value - self.gas_cost_usd_buffered

    @property
    def net_usd_raw(self) -> float | None:
        """Value after the unbuffered gas cost (shown alongside the ranking figure)."""
        if self.status != UNCLAIMED or self.expired:
            return None
        value = self.usd_value
        if value is None or self.gas_estimate_quality == UNAVAILABLE or self.gas_usd is None:
            return None
        return value - self.gas_usd

    @property
    def net_group(self) -> str:
        """Used by the UI/API to keep unknown-net claims in their own group."""
        if self.status != UNCLAIMED:
            return "not_claimable"
        net = self.net_usd
        if net is None:
            return "unknown"
        return "profitable" if net > 0 else "below_gas"

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
    datasets: list[dict] = field(default_factory=list)

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
        gas_buffer_pct: int = 20,
        dataset_stale_days: int = 30,
        max_dataset_bytes: int = MAX_DATASET_BYTES,
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
        self.gas_buffer_pct = gas_buffer_pct
        self.dataset_stale_days = dataset_stale_days
        self.max_dataset_bytes = max_dataset_bytes
        self._rpcs: dict[str, Rpc] = {}
        self._datasets: dict[str, ClaimDataset | None] = {}
        self._provenance: dict[str, dict] = {}
        self._leaf_modes: dict[str, tuple[str, bool] | None] = {}
        self._symbols: dict[str, str] = {}

    # ------------------------------------------------------------------ setup
    def _rpc(self, chain: Chain, report: ScanReport) -> Rpc:
        if chain.key in self._rpcs:
            return self._rpcs[chain.key]
        rpc = self.rpc_factory(chain)
        try:
            if getattr(rpc, "quorum", 1) >= 2 and hasattr(rpc, "request_quorum"):
                actual = int(rpc.request_quorum("eth_chainId"), 16)
            else:
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
            refresh = getattr(rpc, "refresh_endpoint_health", None)
            if callable(refresh):
                try:
                    for stale in refresh(chain.stale_block_seconds):
                        report.warnings.append(
                            f"{chain.name}: RPC {stale['url']} is stale by "
                            f"{stale['lag_seconds']}s and was deprioritised"
                        )
                except RpcError:
                    pass

            self._scan_balances(chain, rpc, normalised, report)
            for drop in self.drops:
                if drop.chain != chain.key or not drop.enabled:
                    continue
                self._check_drop(chain, rpc, drop, normalised, report)

        self._apply_prices(report)
        report.price_error = self.prices.error
        self.cache.flush()
        for dataset in self._datasets.values():
            if dataset is not None:
                dataset.close()
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
            path = Path(eligibility.dataset or "")
            try:
                dataset = parse_dataset_file(
                    path, drop_id=drop.id, max_bytes=self.max_dataset_bytes
                )
                provenance = dataset_provenance(
                    path,
                    drop_id=drop.id,
                    root=dataset.root,
                    entry_count=len(dataset),
                )
                dataset.provenance = provenance.as_dict()
                self._provenance[drop.id] = dataset.provenance
                report.datasets.append(dataset.provenance)
                if is_stale(provenance, self.dataset_stale_days):
                    age = provenance.fetched_at[:10]
                    report.warnings.append(
                        f"{drop.name}: claims dataset is older than "
                        f"{self.dataset_stale_days} days (file dated {age}); verify it is "
                        "still the published distribution before trusting the result"
                    )
                self.verbose(f"  {drop.name}: loaded {len(dataset)} claims from {path.name}")
            except FileNotFoundError:
                report.warnings.append(
                    f"{drop.name}: claims dataset not found ({path.name}) - drop cannot be checked "
                    f"(add the published distribution file to the dataset directory)"
                )
            except (OSError, DatasetError, ValueError) as exc:
                report.warnings.append(
                    f"{drop.name}: claims dataset {path.name} could not be read "
                    f"({exc.__class__.__name__}: {str(exc)[:160]})"
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
                raw = self._call_critical(rpc, drop.distributor, calldata(drop.root_method))
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

        if dataset.provenance is not None:
            dataset.provenance["root_verified"] = bool(root and dataset.root and dataset.root == root)
            dataset.provenance["block_number"] = report.block_numbers.get(chain.key)
            if root:
                dataset.provenance["root"] = "0x" + root.hex()

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
                root=root.hex() if root else None,
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
                        self._claim_row(drop, chain, wallet, NO_ALLOCATION, note=f"no claim record (HTTP {exc.code})", symbol=self._symbols.get(drop.id, drop.name), source=redact_url(url))
                    )
                else:
                    report.claims.append(
                        self._claim_row(drop, chain, wallet, ERROR, note=f"claim API HTTP {exc.code}", symbol=self._symbols.get(drop.id, drop.name), source=redact_url(url))
                    )
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, ERROR, note=f"claim API failed: {describe_error(exc)}", symbol=self._symbols.get(drop.id, drop.name), source=redact_url(url))
                )
                continue

            if not isinstance(payload, dict) or not (
                payload.get("claims") or payload.get("proof") or payload.get("amount")
            ):
                report.claims.append(
                    self._claim_row(drop, chain, wallet, NO_ALLOCATION, note="claim API returned no allocation", symbol=self._symbols.get(drop.id, drop.name), source=redact_url(url))
                )
                continue

            entry = payload.get("claims", payload)
            if not isinstance(entry, dict) or wallet.lower() not in {k.lower() for k in entry}:
                entry = {wallet: entry} if "index" in entry or "amount" in entry else entry
            try:
                dataset = parse_dataset({"claims": entry, "merkleRoot": payload.get("merkleRoot")}, source=redact_url(url))
            except ValueError as exc:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, ERROR, note=f"bad claim payload: {exc}", symbol=self._symbols.get(drop.id, drop.name), source=redact_url(url))
                )
                continue
            claim = dataset.get(wallet)
            if claim is None:
                report.claims.append(
                    self._claim_row(drop, chain, wallet, NO_ALLOCATION, note="claim API has no entry for this address", symbol=self._symbols.get(drop.id, drop.name), source=redact_url(url))
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
                root=root.hex() if root else None,
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

    def _call_critical(self, rpc: Rpc, to: str, data: str) -> Any:
        if getattr(rpc, "quorum", 1) >= 2 and hasattr(rpc, "eth_call_quorum"):
            return rpc.eth_call_quorum(to, data)
        return rpc.eth_call(to, data)

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
        if getattr(rpc, "quorum", 1) >= 2 and hasattr(rpc, "eth_call_quorum"):
            values = [self._call_critical(rpc, drop.distributor, call[1][0]["data"]) for call in calls]
        else:
            values = rpc.batch(calls)
        for position, result in zip(slots, values):
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
        if getattr(rpc, "quorum", 1) >= 2 and hasattr(rpc, "eth_call_quorum"):
            allocations = [
                self._call_critical(rpc, drop.distributor, call[1][0]["data"])
                for call in alloc_calls
            ]
        else:
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
        if claimed_calls and getattr(rpc, "quorum", 1) >= 2 and hasattr(rpc, "eth_call_quorum"):
            claimed_values = [
                self._call_critical(rpc, drop.distributor, call[1][0]["data"])
                for call in claimed_calls
            ]
        else:
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
        """Attach a labelled gas estimate; failure stays ``unavailable``, never zero."""
        if not self.estimate_gas or not row.calldata:
            row.gas_note = "gas estimation disabled for this scan"
            return
        estimate = estimate_claim(
            rpc,
            chain,
            wallet=wallet,
            distributor=row.distributor,
            data=row.calldata,
        )
        row.gas_units = estimate.gas_units
        row.gas_limit = estimate.gas_limit
        row.l1_fee_wei = estimate.l1_fee_wei
        row.gas_estimate_quality = estimate.quality
        row.gas_note = estimate.note
        row.gas_buffer_pct = self.gas_buffer_pct
        if estimate.fee is not None:
            row.fee_per_gas = estimate.fee.gas_price
            row.max_fee_per_gas = estimate.fee.max_fee_per_gas
            row.max_priority_fee_per_gas = estimate.fee.max_priority_fee_per_gas
            row.fee_model = estimate.fee.model
            row.fee_source = estimate.fee.source
        cost_wei = estimate.cost_wei
        if cost_wei is None:
            if estimate.quality == UNAVAILABLE:
                report.warnings.append(
                    f"{row.name}: gas estimate unavailable ({estimate.note or 'no fee data'}); "
                    "net value is unknown"
                )
            return
        row.gas_native = gas_cost_native(cost_wei)
        buffered = buffered_wei(cost_wei, self.gas_buffer_pct)
        row.gas_cost_native_buffered = gas_cost_native(buffered)
        if estimate.quality == PARTIAL:
            report.warnings.append(
                f"{row.name}: gas estimate is partial ({estimate.note or 'L1 component missing'})"
            )

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
                if row.gas_cost_native_buffered is not None:
                    row.gas_cost_usd_buffered = row.gas_cost_native_buffered * native_price


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
    """Default dataset/claim-API fetch; callers can inject a stricter one."""
    payload = safe_fetch_json(url)
    if not isinstance(payload, dict):
        raise FetchError("expected a JSON object")
    return payload
