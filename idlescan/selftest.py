"""Offline end-to-end demo against a mock chain.

``idlescan selftest`` runs the entire pipeline - balance reads, dataset loading,
proof verification against a merkle root, claim-state reads, gas estimation,
report rendering - without touching the network, so it is both a smoke test and
a way to see the output shape before pointing the tool at real addresses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .abi import encode, selector
from .cache import Cache
from .chains import Chain, ChainConfig, NativeAsset, Token
from .merkle import build_proof, compute_root, leaf_hash
from .prices import PriceOracle
from .registry import Airdrop, Eligibility
from .rpc import RpcError
from .scanner import CLAIMED, NO_ALLOCATION, UNCLAIMED, Scanner, ScanReport

WALLET_UNCLAIMED = "0x1111111111111111111111111111111111111111"
WALLET_CLAIMED = "0x2222222222222222222222222222222222222222"
WALLET_EMPTY = "0x3333333333333333333333333333333333333333"

TOKEN = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
DISTRIBUTOR = "0xdddddddddddddddddddddddddddddddddddddddd"

SEL_IS_CLAIMED = selector("isClaimed(uint256)")
SEL_MERKLE_ROOT = selector("merkleRoot()")
SEL_CLAIM = selector("claim(uint256,address,uint256,bytes32[])")
SEL_BALANCE_OF = selector("balanceOf(address)")
SEL_DECIMALS = selector("decimals()")
SEL_SYMBOL = selector("symbol()")


@dataclass
class DemoWorld:
    wallets: list[str]
    native: dict[str, int]
    token_balances: dict[str, int]
    claims: list[tuple[str, int]]
    claimed_indexes: set[int]
    root: bytes


def build_world() -> DemoWorld:
    claims = [
        (WALLET_UNCLAIMED, 300 * 10**18),
        (WALLET_CLAIMED, 125 * 10**18),
        ("0x4444444444444444444444444444444444444444", 42 * 10**18),
    ]
    leaves = [leaf_hash(i, account, amount, encoding="packed") for i, (account, amount) in enumerate(claims)]
    root = compute_root(leaves)
    return DemoWorld(
        wallets=[WALLET_UNCLAIMED, WALLET_CLAIMED, WALLET_EMPTY],
        native={WALLET_UNCLAIMED: 420_000_000_000_000_000, WALLET_EMPTY: 1_500_000_000_000_000_000},
        token_balances={WALLET_UNCLAIMED: 250_000_000, WALLET_EMPTY: 10_000_000},
        claims=claims,
        claimed_indexes={1},
        root=root,
    )


class MockRpc:
    """Implements just enough of :class:`idlescan.rpc.Rpc` for the demo."""

    def __init__(self, world: DemoWorld, *, chain_id: int = 1):
        self.world = world
        self._chain_id = chain_id
        self.calls = 0
        self.batch_supported = True

    # -- plumbing ---------------------------------------------------------
    def request(self, method: str, *params: Any) -> Any:
        self.calls += 1
        handler = getattr(self, f"_{method}", None)
        if handler is None:
            raise RpcError(f"mock does not implement {method}")
        return handler(*params)

    def batch(self, calls: Sequence[tuple[str, Sequence[Any]]]) -> list[Any]:
        out: list[Any] = []
        for method, params in calls:
            try:
                out.append(self.request(method, *params))
            except RpcError as exc:
                out.append(exc)
        return out

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return self.request("eth_call", {"to": to, "data": data}, block)

    def get_code(self, address: str, block: str = "latest") -> str:
        return self.request("eth_getCode", address, block)

    def block_number(self) -> int:
        return int(self.request("eth_blockNumber"), 16)

    # -- chain ------------------------------------------------------------
    def _eth_chainId(self) -> str:
        return hex(self._chain_id)

    def _eth_blockNumber(self) -> str:
        return hex(19_000_000)

    def _eth_gasPrice(self) -> str:
        return hex(12_000_000_000)  # 12 gwei

    def _eth_feeHistory(self, count: str, block: str, percentiles: list[int]) -> dict:
        return {
            "oldestBlock": hex(19_000_000 - 4),
            "baseFeePerGas": [hex(10_000_000_000)] * 6,
            "gasUsedRatio": [0.5] * 5,
            "reward": [[hex(1_000_000_000)] for _ in range(5)],
        }

    def _eth_maxPriorityFeePerGas(self) -> str:
        return hex(1_000_000_000)

    def _eth_getBlockByNumber(self, block: str, full: bool) -> dict:
        return {"number": hex(19_000_000), "baseFeePerGas": hex(10_000_000_000)}

    def _eth_estimateGas(self, tx: dict) -> str:
        if tx.get("to", "").lower() != DISTRIBUTOR:
            raise RpcError("execution reverted")
        return hex(180_000)

    def _eth_getBalance(self, address: str, block: str = "latest") -> str:
        return hex(self.world.native.get(address.lower(), 0))

    def _eth_getCode(self, address: str, block: str = "latest") -> str:
        known = {TOKEN, DISTRIBUTOR}
        return "0x6080604052" if address.lower() in known else "0x"

    def _eth_call(self, tx: dict, block: str = "latest") -> str:
        to = tx.get("to", "").lower()
        data = tx.get("data") or tx.get("input") or ""
        raw = bytes.fromhex(data.removeprefix("0x"))
        sel, args = raw[:4], raw[4:]

        if to == DISTRIBUTOR:
            if sel == SEL_MERKLE_ROOT:
                return "0x" + self.world.root.hex()
            if sel == SEL_IS_CLAIMED:
                index = int.from_bytes(args[:32], "big")
                return "0x" + encode(["bool"], [index in self.world.claimed_indexes]).hex()
            if sel == SEL_CLAIM:
                index = int.from_bytes(args[:32], "big")
                if index in self.world.claimed_indexes:
                    raise RpcError("execution reverted: already claimed")
                return "0x" + encode(["bool"], [True]).hex()
            raise RpcError("execution reverted")

        if to == TOKEN:
            if sel == SEL_BALANCE_OF:
                account = "0x" + args[12:32].hex()
                return "0x" + encode(["uint256"], [self.world.token_balances.get(account.lower(), 0)]).hex()
            if sel == SEL_DECIMALS:
                return "0x" + encode(["uint8"], [6]).hex()
            if sel == SEL_SYMBOL:
                return "0x" + encode(["string"], ["USDC"]).hex()
            raise RpcError("execution reverted")

        raise RpcError("execution reverted")


def build_config() -> tuple[ChainConfig, dict[str, list[Token]], list[Airdrop]]:
    chain = Chain(
        key="ethereum",
        chain_id=1,
        name="Mock Mainnet",
        native=NativeAsset(symbol="ETH", decimals=18, coingecko="ethereum"),
        rpcs=("mock://ethereum",),
        explorer="https://example.invalid",
        coingecko_platform="ethereum",
    )
    chains = ChainConfig(chains={"ethereum": chain})
    tokens = {"ethereum": [Token(chain="ethereum", address=TOKEN, symbol="USDC", decimals=6)]}
    drop = Airdrop(
        id="demo-merkle",
        name="Demo Airdrop (DEMO)",
        chain="ethereum",
        distributor=DISTRIBUTOR,
        token=TOKEN,
        token_decimals=18,
        eligibility=Eligibility(type="merkle_dataset", dataset="(in memory)"),
        notes="Synthetic distributor used by `idlescan selftest`.",
    )
    return chains, tokens, [drop]


def run_selftest(*, verbose: bool = False) -> ScanReport:
    world = build_world()
    chains, tokens, drops = build_config()
    rpc = MockRpc(world)

    scanner = Scanner(
        chains,
        tokens,
        drops,
        cache=Cache(enabled=False),
        prices=PriceOracle(enabled=False),
        rpc_factory=lambda chain: rpc,
        verbose=(lambda message: print(message)) if verbose else None,
    )
    # Inject the in-memory dataset instead of reading a file.
    from .merkle import Claim, ClaimDataset

    dataset = ClaimDataset(
        claims={
            account.lower(): Claim(
                index=index,
                amount=amount,
                proof=tuple(build_proof(index, account, amount, world.claims, encoding="packed")),
            )
            for index, (account, amount) in enumerate(world.claims)
        },
        root=world.root,
        source="selftest (in memory)",
        encoding="packed",
    )
    scanner._datasets["demo-merkle"] = dataset

    report = scanner.scan(["ethereum"], world.wallets)

    # Invariants that must hold for the demo to be meaningful.
    assert any(h.symbol == "ETH" and h.raw == 420_000_000_000_000_000 for h in report.holdings)
    by_wallet = {c.wallet: c for c in report.claims}
    assert by_wallet[WALLET_UNCLAIMED].status == UNCLAIMED, by_wallet[WALLET_UNCLAIMED].status
    assert by_wallet[WALLET_CLAIMED].status == CLAIMED, by_wallet[WALLET_CLAIMED].status
    assert by_wallet[WALLET_EMPTY].status == NO_ALLOCATION, by_wallet[WALLET_EMPTY].status
    assert by_wallet[WALLET_UNCLAIMED].proof_verified is True
    assert by_wallet[WALLET_UNCLAIMED].gas_native and by_wallet[WALLET_UNCLAIMED].gas_native > 0
    return report


def describe_invariants(report: ScanReport) -> str:
    by_wallet = {c.wallet: c for c in report.claims}
    lines = [
        "selftest checks:",
        f"  - unclaimed allocation detected for {WALLET_UNCLAIMED}: {by_wallet[WALLET_UNCLAIMED].status}",
        f"  - already-claimed allocation detected for {WALLET_CLAIMED}: {by_wallet[WALLET_CLAIMED].status}",
        f"  - proof verified against merkle root: {by_wallet[WALLET_UNCLAIMED].proof_verified}",
        f"  - address with no allocation excluded: {by_wallet[WALLET_EMPTY].status}",
        f"  - gas estimate present: {by_wallet[WALLET_UNCLAIMED].gas_native is not None}",
    ]
    return "\n".join(lines)
