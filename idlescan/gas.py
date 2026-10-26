"""Gas and fee estimation that never pretends to be exact.

The v1.1 model was ``eth_estimateGas * eth_gasPrice``.  That is wrong in two
important ways:

* most supported chains price transactions with EIP-1559 (base fee + priority
  fee), so a single ``eth_gasPrice`` number misstates the cost;
* on L2s the L1 data-availability fee can dominate a small claim, and it is not
  part of ``gas_units * fee_per_gas`` on OP-stack and Scroll.

This module derives a fee quote with an explicit source and quality label, adds
the L1 component where the chain exposes it, and returns raw and buffered costs.
The buffer is applied to the *cost used for ranking* so a marginal claim is not
presented as profitable.  A separate 25% headroom is applied to ``gasLimit`` for
the transaction itself.  See ``docs/DECISIONS.md`` D-002 and D-003.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .abi import calldata
from .chains import Chain
from .rpc import Rpc, RpcError

WEI = Decimal(10) ** 18

FULL = "full"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"

# Predeploys documented by the chains themselves, not guessed at scan time.
OP_STACK_GAS_ORACLE = "0x420000000000000000000000000000000000000F"
SCROLL_GAS_ORACLE = "0x5300000000000000000000000000000000000002"

GAS_LIMIT_HEADROOM_PCT = 25


@dataclass(frozen=True)
class FeeQuote:
    """A gas price plus the provenance needed to explain it."""

    model: str
    gas_price: int
    max_fee_per_gas: int
    max_priority_fee_per_gas: int
    source: str
    quality: str
    base_fee: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class GasEstimate:
    gas_units: int | None
    gas_limit: int | None
    fee: FeeQuote | None
    l1_fee_wei: int | None
    quality: str
    note: str | None

    @property
    def cost_wei(self) -> int | None:
        """L2 execution cost plus the L1 data fee, in wei."""
        if self.gas_units is None or self.fee is None:
            return None
        total = self.gas_units * self.fee.gas_price
        if self.l1_fee_wei is not None:
            total += self.l1_fee_wei
        return total


def buffered_wei(cost_wei: int | None, buffer_pct: int) -> int | None:
    """Round the buffered cost up so the buffer is never silently shaved."""
    if cost_wei is None:
        return None
    if buffer_pct <= 0:
        return cost_wei
    numerator = cost_wei * (100 + buffer_pct)
    return (numerator + 99) // 100


def wei_to_native(value: int | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value) / WEI


def _hex_int(value: Any) -> int | None:
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return int(value, 16)
        except ValueError:
            return None
    if isinstance(value, int):
        return value
    return None


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def quote_fees(rpc: Rpc, chain: Chain) -> FeeQuote:
    """Derive a fee quote in the documented order of preference.

    ``eth_feeHistory`` -> ``eth_maxPriorityFeePerGas`` (+ latest base fee) ->
    ``eth_gasPrice``.  Legacy chains go straight to ``eth_gasPrice`` because
    EIP-1559 fields do not exist there.
    """
    if chain.fee_model == "legacy":
        price = _hex_int(rpc.request("eth_gasPrice"))
        if not price or price <= 0:
            raise RpcError(f"{chain.name}: eth_gasPrice returned no usable value")
        return FeeQuote(
            model="legacy",
            gas_price=price,
            max_fee_per_gas=price,
            max_priority_fee_per_gas=0,
            source="eth_gasPrice",
            quality=FULL,
            note="legacy chain: gas price is the full fee",
        )

    errors: list[str] = []
    try:
        history = rpc.request("eth_feeHistory", "0x5", "latest", [50])
        if isinstance(history, dict):
            base_values = history.get("baseFeePerGas") or []
            base = _hex_int(base_values[-1]) if base_values else None
            rewards = [
                _hex_int(item)
                for row in (history.get("reward") or [])
                for item in (row or [])
            ]
            tips = [value for value in rewards if value is not None]
            if base and base > 0 and tips:
                tip = _median(tips)
                return FeeQuote(
                    model="eip1559",
                    gas_price=base + tip,
                    max_fee_per_gas=2 * base + tip,
                    max_priority_fee_per_gas=tip,
                    source="eth_feeHistory",
                    quality=FULL,
                    base_fee=base,
                )
            errors.append("feeHistory lacked a base fee or rewards")
    except (RpcError, TypeError, ValueError) as exc:
        errors.append(f"feeHistory: {exc}")

    try:
        tip = _hex_int(rpc.request("eth_maxPriorityFeePerGas"))
        block = rpc.request("eth_getBlockByNumber", "latest", False)
        base = _hex_int(block.get("baseFeePerGas")) if isinstance(block, dict) else None
        if tip is not None and base and base > 0:
            return FeeQuote(
                model="eip1559",
                gas_price=base + tip,
                max_fee_per_gas=2 * base + tip,
                max_priority_fee_per_gas=tip,
                source="eth_maxPriorityFeePerGas",
                quality=FULL,
                base_fee=base,
            )
        errors.append("maxPriorityFee/baseFee unavailable")
    except (RpcError, TypeError, ValueError) as exc:
        errors.append(f"maxPriorityFeePerGas: {exc}")

    price = _hex_int(rpc.request("eth_gasPrice"))
    if not price or price <= 0:
        raise RpcError(
            f"{chain.name}: no usable fee quote ({'; '.join(errors) or 'empty gasPrice'})"
        )
    # A gasPrice-only node still accepts EIP-1559 fields.  Doubling it gives the
    # transaction room to land if the base fee rises; the cost used for ranking
    # stays at the node's own quote and the quality is marked partial.
    tip = max(price // 10, 1)
    return FeeQuote(
        model="eip1559",
        gas_price=price,
        max_fee_per_gas=price * 2,
        max_priority_fee_per_gas=tip,
        source="eth_gasPrice",
        quality=PARTIAL,
        note=(
            "fee history and priority fee unavailable; EIP-1559 fields derived from "
            "eth_gasPrice (ranking uses the node quote)"
        ),
    )


def _estimate_gas_units(rpc: Rpc, wallet: str, distributor: str, data: str) -> int:
    estimate = rpc.request(
        "eth_estimateGas", {"from": wallet, "to": distributor, "data": data}
    )
    if isinstance(estimate, str):
        units = int(estimate, 16)
    elif isinstance(estimate, int):
        units = estimate
    else:
        raise RpcError("eth_estimateGas returned no usable value")
    if units <= 0:
        raise RpcError("eth_estimateGas returned zero gas")
    return units


def _with_headroom(units: int, headroom_pct: int = GAS_LIMIT_HEADROOM_PCT) -> int:
    return int(math.ceil(units * (100 + headroom_pct) / 100))


def _l1_fee(
    rpc: Rpc, chain: Chain, data: str
) -> tuple[int | None, str, str | None]:
    """Return ``(l1_fee_wei, quality, note)`` for the chain's configured strategy."""
    strategy = chain.l1_fee
    if strategy in ("none", "included"):
        note = (
            "L1 data fee included in the node's gas estimate"
            if strategy == "included"
            else None
        )
        return 0, FULL, note
    if strategy not in ("op_stack", "scroll"):
        return None, PARTIAL, "this chain's L1 data fee is not modelled - cost excludes it"

    if strategy == "scroll":
        oracle = chain.l1_fee_oracle or SCROLL_GAS_ORACLE
        oracle_name = "L1GasPriceOracle"
    else:
        oracle = chain.l1_fee_oracle or OP_STACK_GAS_ORACLE
        oracle_name = "GasPriceOracle"
    try:
        call = calldata("getL1Fee(bytes)", ["bytes"], [data])
        raw = rpc.eth_call(oracle, call)
        value = _hex_int(raw[:66] if isinstance(raw, str) else raw)
        if value is None:
            raise RpcError("empty getL1Fee result")
        return (
            value,
            PARTIAL,
            f"L1 fee from {oracle_name} at {oracle} (unsigned-calldata estimate, not a signed envelope)",
        )
    except (RpcError, ValueError) as exc:
        return (
            None,
            PARTIAL,
            f"{oracle_name} L1 fee unavailable ({exc}); cost excludes the L1 component",
        )


def estimate_claim(
    rpc: Rpc,
    chain: Chain,
    *,
    wallet: str,
    distributor: str,
    data: str,
) -> GasEstimate:
    """Estimate a claim's gas, fee and L1 component.

    Never raises for an ordinary estimation failure: the caller records
    ``unavailable`` and keeps the claim's net value unknown instead of zero.
    """
    try:
        fee = quote_fees(rpc, chain)
    except (RpcError, TypeError, ValueError) as exc:
        return GasEstimate(None, None, None, None, UNAVAILABLE, f"fee quote failed: {exc}")
    try:
        units = _estimate_gas_units(rpc, wallet, distributor, data)
    except (RpcError, TypeError, ValueError) as exc:
        return GasEstimate(None, None, fee, None, UNAVAILABLE, f"gas estimate failed: {exc}")

    l1_fee, l1_quality, l1_note = _l1_fee(rpc, chain, data)
    quality = UNAVAILABLE
    if fee.quality != UNAVAILABLE:
        quality = FULL if fee.quality == FULL and l1_quality == FULL else PARTIAL
    notes = [note for note in (fee.note, l1_note) if note]
    return GasEstimate(
        gas_units=units,
        gas_limit=_with_headroom(units),
        fee=fee,
        l1_fee_wei=l1_fee,
        quality=quality,
        note="; ".join(notes) or None,
    )


def gas_cost_native(cost_wei: int | None) -> float | None:
    """Native-token cost as a float for the existing REAL columns."""
    value = wei_to_native(cost_wei)
    return float(value) if value is not None else None
