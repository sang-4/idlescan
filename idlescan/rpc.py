"""Small JSON-RPC client with failover, retries and batching.

Uses only the standard library so the tool runs on a bare Python 3.10+ without
installing web3.  Every call is read-only (``eth_call``, ``eth_getBalance``,
``eth_chainId``, ...); nothing here can sign or send a transaction.
"""

from __future__ import annotations

import itertools
import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Iterable, Sequence


class RpcError(RuntimeError):
    """Transport failed or the node returned a JSON-RPC error."""

    def __init__(self, message: str, *, transient: bool = False, code: int | None = None):
        super().__init__(message)
        self.transient = transient
        self.code = code


class Revert(RpcError):
    """The EVM rejected the call (bad selector, wrong args, or a revert)."""


_TRANSIENT_CODES = {-32005, -32603, -32000}  # rate-limit / node busy shapes

_ids = itertools.count(1)


class Rpc:
    """A chain connection that fails over between a list of endpoints."""

    def __init__(
        self,
        urls: Sequence[str],
        *,
        timeout: float = 20.0,
        retries: int = 3,
        batch_size: int = 40,
        sleep=time.sleep,
    ):
        if not urls:
            raise ValueError("at least one RPC URL is required")
        self.urls = list(urls)
        self.timeout = timeout
        self.retries = max(1, retries)
        self.batch_size = max(1, batch_size)
        self._sleep = sleep
        self._preferred = 0
        self._supports_batch = True
        self.chain_id: int | None = None

    # ------------------------------------------------------------------ http
    def _post(self, url: str, payload: Any) -> Any:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                "user-agent": "idlescan/1.0 (read-only portfolio checker)",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            snippet = raw.strip()[:120].replace("\n", " ")
            raise RpcError(f"{url} returned non-JSON ({snippet!r})") from exc

    @staticmethod
    def _raise_for_item(item: dict, url: str) -> None:
        error = item.get("error")
        if not error:
            return
        code = error.get("code")
        message = error.get("message", "unknown node error")
        if "revert" in message.lower() or code == 3:
            raise Revert(message, code=code)
        raise RpcError(
            f"{url}: {message}", transient=code in _TRANSIENT_CODES, code=code
        )

    def _attempt(self, method: str, params: Iterable[Any]) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries):
            # Rotate through endpoints so one dead provider cannot stall a scan.
            for step in range(len(self.urls)):
                index = (self._preferred + step) % len(self.urls)
                url = self.urls[index]
                try:
                    payload = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": list(params)}
                    data = self._post(url, payload)
                    if isinstance(data, list):  # some nodes answer a single call with an array
                        data = data[0] if data else {}
                    self._raise_for_item(data, url)
                    self._preferred = index
                    return data.get("result")
                except Revert:
                    raise
                except (urllib.error.URLError, TimeoutError, OSError, RpcError) as exc:
                    last = exc
                    transient = getattr(exc, "transient", True)
                    if not transient:
                        raise
                if attempt == 0:
                    self._sleep(0.2 + random.random() * 0.3)
            if attempt + 1 < self.retries:
                self._sleep(0.4 * (2**attempt) + random.random() * 0.3)
        raise RpcError(f"all endpoints failed for {method}: {last}")

    # ------------------------------------------------------------- interface
    def request(self, method: str, *params: Any) -> Any:
        return self._attempt(method, params)

    def batch(self, calls: Sequence[tuple[str, Sequence[Any]]]) -> list[Any]:
        """Run calls concurrently; per-item failures come back as exceptions."""
        results: list[Any] = []
        for chunk in _chunks(list(calls), self.batch_size):
            if self._supports_batch:
                try:
                    results.extend(self._batch_chunk(chunk))
                    continue
                except (RpcError, OSError):
                    self._supports_batch = False  # fall back for the rest of the run
            results.extend(self._sequential(chunk))
        return results

    def _batch_chunk(self, chunk: Sequence[tuple[str, Sequence[Any]]]) -> list[Any]:
        request = [
            {"jsonrpc": "2.0", "id": index, "method": method, "params": list(params)}
            for index, (method, params) in enumerate(chunk)
        ]
        last: Exception | None = None
        for attempt in range(min(2, self.retries)):
            try:
                payload = self._post(self.urls[self._preferred], request)
            except (urllib.error.URLError, TimeoutError, OSError, RpcError) as exc:
                last = exc
                self._sleep(0.3 * (attempt + 1))
                continue
            if not isinstance(payload, list):
                raise RpcError("node rejected batch request")
            by_id: dict[int, dict] = {}
            for item in payload:
                if isinstance(item, dict) and "id" in item:
                    by_id[item["id"]] = item
            out: list[Any] = []
            for index in range(len(chunk)):
                item = by_id.get(index)
                if item is None:
                    out.append(RpcError("missing batch response"))
                    continue
                try:
                    self._raise_for_item(item, self.urls[self._preferred])
                    out.append(item.get("result"))
                except RpcError as exc:
                    out.append(exc)
            return out
        raise RpcError(f"batch failed: {last}")

    def _sequential(self, chunk: Sequence[tuple[str, Sequence[Any]]]) -> list[Any]:
        out: list[Any] = []
        for method, params in chunk:
            try:
                out.append(self._attempt(method, params))
            except RpcError as exc:
                out.append(exc)
        return out

    # ------------------------------------------------------------------ views
    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return self.request("eth_call", {"to": to, "data": data}, block)

    def get_balance(self, address: str, block: str = "latest") -> int:
        return int(self.request("eth_getBalance", address, block), 16)

    def get_code(self, address: str, block: str = "latest") -> str:
        return self.request("eth_getCode", address, block)

    def block_number(self) -> int:
        return int(self.request("eth_blockNumber"), 16)

    def gas_price(self) -> int:
        return int(self.request("eth_gasPrice"), 16)


def rpc_chain_id(url: str, timeout: float = 10.0) -> int:
    client = Rpc([url], timeout=timeout, retries=1)
    return int(client.request("eth_chainId"), 16)


def _chunks(items: list[Any], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def describe_error(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    return text[:300]
