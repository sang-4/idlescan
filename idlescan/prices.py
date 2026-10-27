"""Token pricing.

DefiLlama's ``coins`` endpoint is the primary source: no API key, one request for
every asset in the scan (cheap on rate limits), and it covers all the chains we
support.  CoinGecko is used only for native coins on chains DefiLlama does not
index.

Any failure degrades to "price unknown" rather than a wrong number - a scan with
no prices still reports raw token amounts.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
from typing import Any, Callable, Iterable, Mapping

from .cache import Cache
from .safe_http import FetchError, safe_fetch_json

LLAMA = "https://coins.llama.fi/prices/current"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"
PRICE_TTL = 600
NATIVE_PLACEHOLDER = "0x0000000000000000000000000000000000000000"


class PriceOracle:
    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout: float = 15.0,
        cache: Cache | None = None,
        fetcher: Callable[[str], Any] | None = None,
    ):
        self.enabled = enabled
        self.timeout = timeout
        self.cache = cache or Cache(enabled=False)
        self.fetcher = fetcher or (lambda url: safe_fetch_json(url, timeout=self.timeout))
        self.error: str | None = None

    # ------------------------------------------------------------------ http
    def _get(self, url: str) -> Any:
        return self.fetcher(url)

    # ------------------------------------------------------------------ core
    def quote(self, assets: Mapping[str, str]) -> dict[str, float]:
        """Map ``{our_key: provider_id}`` to prices in USD.

        ``provider_id`` is a DefiLlama id such as ``ethereum:0xabc…`` or
        ``coingecko:ethereum``.
        """
        if not self.enabled or not assets:
            return {}

        result: dict[str, float] = {}
        missing: dict[str, str] = {}
        for key, provider_id in assets.items():
            cached = self.cache.get(f"price:{provider_id}", max_age=PRICE_TTL)
            if isinstance(cached, (int, float)):
                result[key] = float(cached)
            else:
                missing[key] = provider_id
        if not missing:
            return result

        llama_ids = [pid for pid in missing.values() if ":" in pid and not pid.startswith("coingecko:")]
        cg_ids = sorted({pid.split(":", 1)[1] for pid in missing.values() if pid.startswith("coingecko:")})
        fetched: dict[str, float] = {}

        if llama_ids:
            fetched.update(self._quote_llama(llama_ids))
        if cg_ids:
            fetched.update(self._quote_coingecko(cg_ids))

        for key, provider_id in missing.items():
            price = fetched.get(provider_id.lower()) or fetched.get(provider_id)
            if price is None and provider_id.startswith("coingecko:"):
                price = fetched.get("coingecko:" + provider_id.split(":", 1)[1].lower())
            if price is None:
                continue
            result[key] = price
            self.cache.set(f"price:{provider_id}", price)
        return result

    def _quote_llama(self, ids: Iterable[str]) -> dict[str, float]:
        url = f"{LLAMA}/{urllib.parse.quote(','.join(sorted(ids)), safe=':,')}"
        try:
            payload = self._get(url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, FetchError) as exc:
            self.error = f"DefiLlama pricing unavailable ({exc})"
            return {}
        coins = payload.get("coins") if isinstance(payload, dict) else None
        if not isinstance(coins, dict):
            return {}
        out: dict[str, float] = {}
        for coin_id, data in coins.items():
            if isinstance(data, dict) and isinstance(data.get("price"), (int, float)):
                out[coin_id.lower()] = float(data["price"])
        if not out:
            self.error = "DefiLlama returned no prices"
        return out

    def _quote_coingecko(self, ids: Iterable[str]) -> dict[str, float]:
        url = f"{COINGECKO}?ids={urllib.parse.quote(','.join(sorted(ids)))}&vs_currencies=usd"
        try:
            payload = self._get(url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, FetchError) as exc:
            if self.error is None:
                self.error = f"CoinGecko pricing unavailable ({exc})"
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            "coingecko:" + coin.lower(): float(data["usd"])
            for coin, data in payload.items()
            if isinstance(data, dict) and isinstance(data.get("usd"), (int, float))
        }


def provider_id_for(chain, address: str | None) -> str | None:
    """DefiLlama id for a native coin or ERC-20 on ``chain``."""
    if address:
        prefix = chain.defillama or chain.coingecko_platform
        if not prefix:
            return None
        return f"{prefix}:{address.lower()}"
    if chain.native.coingecko:
        return f"coingecko:{chain.native.coingecko}"
    if chain.defillama:
        return f"{chain.defillama}:{NATIVE_PLACEHOLDER}"
    return None
