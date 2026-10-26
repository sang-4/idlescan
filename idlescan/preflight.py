"""Fresh, fail-closed checks immediately before an unsigned claim is handed off.

A snapshot can be seconds or days old.  This module re-reads the chain at the
moment of preparation, decodes the calldata independently, and refuses to return
anything that does not match the verified position.  The server still never
signs or broadcasts - it only hands the user a transaction they can inspect.
"""

from __future__ import annotations

import re
from typing import Any

from .abi import AbiError, decode, selector
from .registry import Airdrop
from .rpc import Revert, Rpc, RpcError, describe_error

_SIGNATURE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\(([^)]*)\)\s*$")
_REVERT_STRING = "08c379a0"
_REVERT_PANIC = "4e487b71"


class PreflightError(RuntimeError):
    """A preparation-time check failed; ``status`` maps to the HTTP scheme."""

    def __init__(self, message: str, *, status: int = 409):
        super().__init__(message)
        self.status = status


class PreflightUnavailable(PreflightError):
    def __init__(self, message: str):
        super().__init__(message, status=503)


def parse_signature(signature: str) -> tuple[str, list[str]]:
    match = _SIGNATURE.match(signature or "")
    if not match:
        raise PreflightError(f"unsupported claim signature {signature!r}", status=400)
    types = [part.strip() for part in match.group(2).split(",") if part.strip()]
    return match.group(1), types


def decode_claim_call(drop: Airdrop, data: str) -> dict[str, Any]:
    """Decode calldata independently and return a structured, human-readable summary."""
    if not isinstance(data, str) or not data.startswith("0x") or len(data) < 10:
        raise PreflightError("claim calldata is missing or malformed", status=400)
    name, types = parse_signature(drop.claim_method)
    expected = "0x" + selector(drop.claim_method).hex()
    if data[:10].lower() != expected:
        raise PreflightError(
            f"calldata selector {data[:10]} does not match {drop.claim_method} ({expected})",
            status=400,
        )
    try:
        values = decode(types, data[10:])
    except (AbiError, ValueError) as exc:
        raise PreflightError(f"claim calldata could not be decoded: {exc}", status=400) from exc

    summary: dict[str, Any] = {
        "function": drop.claim_method,
        "selector": expected,
        "types": types,
        "values": [],
        "index": None,
        "account": None,
        "amount": None,
        "proof": None,
    }
    uint_count = 0
    for type_name, value in zip(types, values):
        rendered: Any = value
        if type_name == "address":
            summary["account"] = str(value).lower()
        elif type_name == "bytes32[]":
            summary["proof"] = [("0x" + item.hex()) if isinstance(item, bytes) else str(item) for item in value]
            rendered = len(value)
        elif type_name.startswith("uint"):
            uint_count += 1
            if summary["index"] is None:
                summary["index"] = int(value)
            else:
                summary["amount"] = int(value)
        summary["values"].append({"type": type_name, "value": rendered})
    if summary["account"] is None:
        raise PreflightError("claim function has no account argument", status=400)
    if summary["amount"] is None:
        raise PreflightError("claim function has no amount argument", status=400)
    if summary["proof"] is None:
        raise PreflightError("claim function has no proof argument", status=400)
    return summary


def _root(value: str | bytes | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.hex()
    return value.lower().removeprefix("0x")


def _revert_message(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    marker = lowered.find("0x" + _REVERT_STRING)
    if marker >= 0:
        raw = text[marker + 2 :]
        try:
            payload = bytes.fromhex(raw.split()[0])
            if payload[:4].hex() == _REVERT_STRING:
                offset = int.from_bytes(payload[4:36], "big")
                length = int.from_bytes(payload[offset : offset + 32], "big")
                message = payload[offset + 32 : offset + 32 + length].decode("utf-8", "replace")
                if message:
                    return f"reverted: {message}"
        except (ValueError, UnicodeDecodeError):
            pass
    marker = lowered.find("0x" + _REVERT_PANIC)
    if marker >= 0:
        return "reverted with a panic code"
    return f"reverted: {text}" if text else "reverted without a reason"


def _call(rpc: Rpc, to: str, data: str, *, from_address: str | None = None) -> str:
    tx: dict[str, Any] = {"to": to, "data": data}
    if from_address:
        tx["from"] = from_address
    result = rpc.request("eth_call", tx, "latest")
    if not isinstance(result, str):
        raise PreflightUnavailable("RPC returned no result for eth_call")
    return result


def _is_claimed(rpc: Rpc, drop: Airdrop, call: dict[str, Any]) -> bool:
    try:
        if drop.claimed_arg == "index":
            data = selector(drop.claimed_method)
            raw = "0x" + data.hex() + int(call["index"] or 0).to_bytes(32, "big").hex()
        else:
            data = selector(drop.claimed_method)
            raw = "0x" + data.hex() + bytes.fromhex(call["account"][2:].rjust(64, "0")).hex()
        result = _call(rpc, drop.distributor, raw)
    except (RpcError, ValueError) as exc:
        raise PreflightUnavailable(
            f"could not re-read claim state ({describe_error(exc)})"
        ) from exc
    if len(result) >= 66:
        return int(result[2:66], 16) != 0
    raise PreflightUnavailable("claim-state call returned an unexpected value")


def run_preflight(
    rpc: Rpc,
    drop: Airdrop,
    row: dict[str, Any],
    *,
    now: Any = None,
) -> dict[str, Any]:
    """Run every check and return the evidence the API includes in its response."""
    del now  # expiry is checked by the service with its clock helper
    wallet = str(row["wallet"]).lower()
    data = str(row.get("calldata") or "")
    call = decode_claim_call(drop, data)

    if call["account"] != wallet:
        raise PreflightError(
            f"calldata account {call['account']} does not match wallet {wallet}", status=400
        )
    try:
        expected_amount = int(str(row.get("amount_raw") or "0"))
    except ValueError:
        raise PreflightError("snapshot amount is not an integer", status=409) from None
    if call["amount"] != expected_amount:
        raise PreflightError(
            f"calldata amount {call['amount']} does not match verified position "
            f"{expected_amount}",
            status=400,
        )
    if drop.claim_payable and not drop.allow_payable:
        raise PreflightError(
            "registry entry declares a payable claim function but does not allow it; "
            "refusing to prepare a transaction that may send ETH",
            status=400,
        )

    try:
        code = rpc.get_code(drop.distributor)
    except RpcError as exc:
        raise PreflightUnavailable(f"could not read distributor code ({describe_error(exc)})") from exc
    if not code or code == "0x":
        raise PreflightError(
            f"no contract code at {drop.distributor} on {drop.chain}; the registry entry "
            "may be stale or wrong",
            status=409,
        )

    root_now: str | None = None
    if drop.root_method:
        try:
            raw_root = _call(rpc, drop.distributor, "0x" + selector(drop.root_method).hex())
        except RpcError as exc:
            raise PreflightUnavailable(
                f"could not re-read {drop.root_method} ({describe_error(exc)})"
            ) from exc
        if raw_root and raw_root != "0x":
            root_now = _root(raw_root[:66])
        snapshot_root = _root(row.get("root"))
        if root_now and snapshot_root and root_now != snapshot_root:
            raise PreflightError(
                "the distributor's merkle root changed since the snapshot; the stored "
                "proofs are stale. Run a fresh scan before preparing this claim.",
                status=409,
            )

    if _is_claimed(rpc, drop, call):
        raise PreflightError(
            "this allocation is already claimed on chain; the snapshot is stale",
            status=409,
        )

    try:
        _call(rpc, drop.distributor, data, from_address=wallet)
    except RpcError as exc:
        if isinstance(exc, Revert) or "revert" in str(exc).lower():
            raise PreflightError(
                f"claim simulation failed: {_revert_message(exc)}", status=409
            ) from exc
        raise PreflightUnavailable(
            f"claim simulation could not run ({describe_error(exc)})"
        ) from exc

    try:
        block = rpc.block_number()
    except RpcError:
        block = None

    return {
        "call": call,
        "block_number": block,
        "root": root_now,
        "distributor_code": code[:10] if isinstance(code, str) else None,
        "payable": bool(drop.claim_payable),
        "value": "0x0",
        "provenance": {
            "official_source_url": drop.official_source_url,
            "announcement_url": drop.announcement_url,
            "contract_source_url": drop.contract_source_url,
            "verified_at": drop.verified_at,
            "verified_by": drop.verified_by,
            "verified_on": drop.verified_on,
            "notes": drop.notes,
        },
    }
