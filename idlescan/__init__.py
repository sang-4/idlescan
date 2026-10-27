"""idlescan - read-only idle balance & unclaimed airdrop scanner.

    from idlescan import scan

    report = scan(["0xabc..."], chains=["ethereum", "base"])
    print(report.total_value)
"""

from __future__ import annotations

from typing import Sequence

from .chains import load_chains, load_tokens
from .registry import load_registry
from .scanner import Scanner, ScanReport

__version__ = "1.2.0"
__all__ = ["scan", "Scanner", "ScanReport", "__version__"]


def scan(
    addresses: Sequence[str],
    *,
    chains: Sequence[str] | None = None,
    registry=None,
    chains_file=None,
    tokens_file=None,
    prices: bool = True,
    gas: bool = True,
    cache: bool = True,
) -> ScanReport:
    """Convenience wrapper: scan ``addresses`` with the bundled configuration."""
    from .cache import Cache
    from .prices import PriceOracle

    config = load_chains(chains_file)
    tokens = load_tokens(config, tokens_file)
    drops = load_registry(registry, config)
    store = Cache(enabled=cache)
    scanner = Scanner(
        config,
        tokens,
        drops,
        cache=store,
        prices=PriceOracle(enabled=prices, cache=store),
        estimate_gas=gas,
    )
    return scanner.scan(list(chains or []), addresses)
