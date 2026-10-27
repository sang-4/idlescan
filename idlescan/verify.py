"""``idlescan verify``: does each registry entry still point at what it claims?

Catches the failure modes that silently produce wrong reports:
  * distributor address has no code (typo, wrong chain, contract self-destructed)
  * the claim/root methods do not exist or revert
  * the bundled dataset's merkle root does not match the contract
  * the token address is not a contract
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .abi import AbiError, calldata
from .chains import Chain, ChainConfig
from .merkle import parse_dataset, verify_proof
from .registry import Airdrop
from .rpc import Rpc, RpcError, describe_error


@dataclass
class Check:
    name: str
    ok: bool | None
    detail: str = ""


@dataclass
class DropVerification:
    drop_id: str
    name: str
    chain: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok is not False for check in self.checks)

    @property
    def symbol(self) -> str:
        if not self.ok:
            return "FAIL"
        if any(check.ok is None for check in self.checks):
            return "WARN"
        return "OK"


def verify_drops(
    drops: Sequence[Airdrop],
    chains: ChainConfig,
    *,
    rpc_factory: Callable[[Chain], Rpc] | None = None,
) -> list[DropVerification]:
    factory = rpc_factory or (lambda chain: Rpc(list(chain.rpcs)))
    results: list[DropVerification] = []
    clients: dict[str, Rpc] = {}

    for drop in drops:
        result = DropVerification(drop_id=drop.id, name=drop.name, chain=drop.chain)
        results.append(result)
        if not drop.enabled:
            result.checks.append(Check("enabled", None, "entry disabled in registry"))
            continue
        try:
            chain = chains.get(drop.chain)
        except Exception as exc:  # ConfigError
            result.checks.append(Check("chain", False, str(exc)))
            continue

        if chain.key not in clients:
            clients[chain.key] = factory(chain)
        rpc = clients[chain.key]

        try:
            result.checks.append(Check("chain id", True, f"{int(rpc.request('eth_chainId'), 16)} ({chain.name})"))
        except (RpcError, TypeError, ValueError) as exc:
            result.checks.append(Check("chain id", False, describe_error(exc)))
            continue

        try:
            code = rpc.get_code(drop.distributor)
            has_code = isinstance(code, str) and len(code) > 2
            result.checks.append(
                Check(
                    "distributor code",
                    has_code,
                    f"{drop.distributor} ({len(code) // 2} bytes)" if has_code else "no contract at this address",
                )
            )
            if not has_code:
                continue
        except RpcError as exc:
            result.checks.append(Check("distributor code", False, describe_error(exc)))
            continue

        # claimed / root method probes
        root: bytes | None = None
        if drop.root_method:
            try:
                raw = rpc.eth_call(drop.distributor, calldata(drop.root_method))
                if isinstance(raw, str) and len(raw) >= 66:
                    root = bytes.fromhex(raw[2:66])
                    result.checks.append(Check(drop.root_method, True, "0x" + root.hex()))
                else:
                    result.checks.append(Check(drop.root_method, False, "returned no data"))
            except (RpcError, AbiError) as exc:
                result.checks.append(Check(drop.root_method, False, describe_error(exc)))

        probe_arg: int | str = 0
        if "address" in drop.claimed_method:
            probe_arg = "0x0000000000000000000000000000000000000001"
        try:
            raw = rpc.eth_call(
                drop.distributor,
                calldata(drop.claimed_method, ["uint256" if isinstance(probe_arg, int) else "address"], [probe_arg]),
            )
            ok = isinstance(raw, str) and len(raw) >= 66
            result.checks.append(Check(drop.claimed_method, ok, "returns bool" if ok else "returned no data"))
        except (RpcError, AbiError) as exc:
            result.checks.append(Check(drop.claimed_method, False, describe_error(exc)))

        if drop.token:
            try:
                token_code = rpc.get_code(drop.token)
                has_token = isinstance(token_code, str) and len(token_code) > 2
                if has_token:
                    raw = rpc.eth_call(drop.token, calldata("decimals()"))
                    decimals = int(raw[2:66], 16) if isinstance(raw, str) and len(raw) >= 66 else None
                    result.checks.append(Check("token", decimals == drop.token_decimals, f"decimals={decimals}"))
                else:
                    result.checks.append(Check("token", False, "no contract at token address"))
            except (RpcError, AbiError, ValueError) as exc:
                result.checks.append(Check("token", False, describe_error(exc)))

        eligibility = drop.eligibility
        if eligibility is None:
            result.checks.append(Check("eligibility", None, "no eligibility source; claim state only"))
        elif eligibility.type == "allocation_view":
            try:
                raw = rpc.eth_call(
                    drop.distributor,
                    calldata(eligibility.method, ["address"], ["0x0000000000000000000000000000000000000001"]),
                )
                ok = isinstance(raw, str) and len(raw) >= 66
                result.checks.append(Check(eligibility.method, ok, "returns uint256" if ok else "returned no data"))
            except (RpcError, AbiError) as exc:
                result.checks.append(Check(eligibility.method, False, describe_error(exc)))
        elif eligibility.type == "dataset_url":
            result.checks.append(Check("dataset url", None, eligibility.url or ""))
        else:
            path = eligibility.dataset or ""
            try:
                dataset = parse_dataset(json.loads(Path(path).read_text()), source=path)
            except FileNotFoundError:
                result.checks.append(Check("dataset", None, f"not present yet: {path}"))
                continue
            except (OSError, ValueError) as exc:
                result.checks.append(Check("dataset", False, f"{path}: {exc}"))
                continue
            result.checks.append(Check("dataset", True, f"{len(dataset)} claims from {path}"))
            if root is not None:
                if dataset.root and dataset.root != root:
                    result.checks.append(Check("dataset root", False, "dataset root != on-chain root"))
                else:
                    result.checks.append(Check("dataset root", True, "matches contract"))
                proof_ok = detect_proof_mode(dataset, root)
                result.checks.append(
                    Check(
                        "proofs",
                        True if proof_ok else False,
                        f"leaf {proof_ok[0]}{' (double hashed)' if proof_ok[1] else ''}"
                        if proof_ok
                        else "no leaf encoding reproduces the root",
                    )
                )
            else:
                result.checks.append(Check("dataset root", None, "on-chain root unavailable"))

    return results


def detect_proof_mode(dataset, root: bytes, limit: int = 5) -> tuple[str, bool] | None:
    samples = [(addr, claim) for addr, claim in dataset.claims.items() if claim.proof][:limit]
    if not samples:
        # A one-leaf tree has an empty proof and is still verifiable.
        samples = list(dataset.claims.items())[:limit]
    if not samples:
        return None
    for encoding, double in (("packed", False), ("abi", False), ("packed", True), ("abi", True)):
        if all(
            verify_proof(
                claim.index,
                addr,
                claim.amount,
                claim.proof,
                root,
                encoding=encoding,
                double_hash=double,
                sort_pairs=dataset.sort_pairs,
            )
            for addr, claim in samples
        ):
            return encoding, double
    return None
