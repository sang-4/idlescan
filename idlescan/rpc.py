"""Small JSON-RPC client with failover, retries and batching.

Uses only the standard library so the tool runs on a bare Python 3.10+ without
installing web3.  Every call is read-only (``eth_call``, ``eth_getBalance``,
``eth_chainId``, ...); nothing here can sign or send a transaction.
"""

from __future__ import annotations

import itertools
import json
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .redaction import redact_text, redact_url


class RpcError(RuntimeError):
    """Transport failed or the node returned a JSON-RPC error."""

    def __init__(self, message: str, *, transient: bool = False, code: int | None = None):
        super().__init__(message)
        self.transient = transient
        self.code = code


class Revert(RpcError):
    """The EVM rejected the call (bad selector, wrong args, or a revert)."""


class QuorumError(RpcError):
    """Configured endpoints did not agree on a critical read."""


def _redacted(exc: BaseException) -> RpcError:
    """Re-raise an RPC failure with credentials scrubbed from its message."""
    cls = Revert if isinstance(exc, Revert) else RpcError
    return cls(
        redact_text(str(exc) or exc.__class__.__name__),
        transient=bool(getattr(exc, "transient", False)),
        code=getattr(exc, "code", None),
    )


@dataclass
class EndpointState:
    url: str
    label: str = ""
    successes: int = 0
    errors: int = 0
    latency_ms: float | None = None
    last_success: float | None = None
    last_error: str | None = None
    block_number: int | None = None
    block_timestamp: int | None = None
    stale: bool = False

    @property
    def error_rate(self) -> float:
        total = self.successes + self.errors
        return (self.errors / total) if total else 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "url": redact_url(self.url),
            "label": self.label,
            "successes": self.successes,
            "errors": self.errors,
            "error_rate": round(self.error_rate, 3),
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "last_success": self.last_success,
            "last_error": redact_text(self.last_error) if self.last_error else None,
            "block_number": self.block_number,
            "block_timestamp": self.block_timestamp,
            "stale": self.stale,
        }


class EndpointHealth:
    """In-memory endpoint health shared by every client for a chain."""

    def __init__(self) -> None:
        self._states: dict[str, EndpointState] = {}
        self._lock = threading.Lock()

    def state(self, url: str, label: str = "") -> EndpointState:
        with self._lock:
            state = self._states.get(url)
            if state is None:
                state = EndpointState(url=url, label=label)
                self._states[url] = state
            elif label and not state.label:
                state.label = label
            return state

    def success(self, url: str, latency_ms: float, *, label: str = "") -> None:
        state = self.state(url, label)
        with self._lock:
            state.successes += 1
            state.latency_ms = latency_ms
            state.last_success = time.time()
            state.last_error = None

    def failure(self, url: str, error: str, *, label: str = "") -> None:
        state = self.state(url, label)
        with self._lock:
            state.errors += 1
            state.last_error = redact_text(error)[:500]

    def mark_block(self, url: str, number: int | None, timestamp: int | None, *, stale: bool) -> None:
        state = self.state(url)
        with self._lock:
            state.block_number = number
            state.block_timestamp = timestamp
            state.stale = stale

    def score(self, url: str) -> tuple[int, float, float]:
        state = self.state(url)
        return (1 if state.stale else 0, state.error_rate, state.latency_ms or 10_000.0)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            states = list(self._states.values())
        return [state.snapshot() for state in sorted(states, key=lambda item: item.url)]


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
        quorum: int = 1,
        health: EndpointHealth | None = None,
        label: str = "",
        post=None,
    ):
        if not urls:
            raise ValueError("at least one RPC URL is required")
        self.urls = list(dict.fromkeys(urls))
        self.quorum = max(1, int(quorum))
        self.health = health or EndpointHealth()
        self.label = label
        self._post_override = post
        self.timeout = timeout
        self.retries = max(1, retries)
        self.batch_size = max(1, batch_size)
        self._sleep = sleep
        self._preferred = 0
        self._supports_batch = True
        self.chain_id: int | None = None

    # ------------------------------------------------------------------ http
    def _post(self, url: str, payload: Any) -> Any:
        if self._post_override is not None:
            return self._post_override(url, payload)
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
        raise RpcError(f"{redact_url(url)}: {message}", transient=code in _TRANSIENT_CODES, code=code)

    def _attempt(self, method: str, params: Iterable[Any]) -> Any:
        last: Exception | None = None
        for attempt in range(self.retries):
            # Rotate through endpoints so one dead provider cannot stall a scan.
            for step in range(len(self.urls)):
                index = (self._preferred + step) % len(self.urls)
                url = self.urls[index]
                try:
                    started = time.monotonic()
                    payload = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": list(params)}
                    data = self._post(url, payload)
                    if isinstance(data, list):  # some nodes answer a single call with an array
                        data = data[0] if data else {}
                    self._raise_for_item(data, url)
                    self._preferred = index
                    self.health.success(url, (time.monotonic() - started) * 1000, label=self.label)
                    return data.get("result")
                except Revert:
                    raise
                except (urllib.error.URLError, TimeoutError, OSError, RpcError) as exc:
                    last = exc
                    self.health.failure(url, str(exc), label=self.label)
                    transient = getattr(exc, "transient", True)
                    if not transient:
                        raise _redacted(exc) from None
                if attempt == 0:
                    self._sleep(0.2 + random.random() * 0.3)
            if attempt + 1 < self.retries:
                self._sleep(0.4 * (2**attempt) + random.random() * 0.3)
        raise RpcError(f"all endpoints failed for {method}: {redact_text(str(last))}")

    # ------------------------------------------------------------- interface
    def request(self, method: str, *params: Any) -> Any:
        return self._attempt(method, params)

    def _single(self, url: str, method: str, params: Sequence[Any]) -> Any:
        started = time.monotonic()
        payload = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": list(params)}
        try:
            data = self._post(url, payload)
            if isinstance(data, list):
                data = data[0] if data else {}
            self._raise_for_item(data, url)
        except Revert:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, RpcError) as exc:
            self.health.failure(url, str(exc), label=self.label)
            raise _redacted(exc) from None
        self.health.success(url, (time.monotonic() - started) * 1000, label=self.label)
        return data.get("result")

    @staticmethod
    def _comparable(value: Any) -> str:
        if isinstance(value, str):
            return value.lower().removeprefix("0x") if value.startswith("0x") else value.lower()
        return json.dumps(value, sort_keys=True, default=str)

    def _ordered_urls(self) -> list[str]:
        return sorted(self.urls, key=self.health.score)

    def request_quorum(self, method: str, *params: Any, min_agree: int | None = None) -> Any:
        """Read one value that at least ``min_agree`` endpoints agree on.

        Distinct endpoints only.  On disagreement the error names each endpoint
        (redacted) and the value it returned, so a bad provider is obvious.
        """
        required = max(2, min_agree or self.quorum)
        if len(self.urls) < 2 or required <= 1:
            return self.request(method, *params)
        required = min(required, len(self.urls))
        seen: dict[str, list[str]] = {}
        raw_by_value: dict[str, Any] = {}
        failures: list[str] = []
        for url in self._ordered_urls():
            try:
                value = self._single(url, method, params)
            except RpcError as exc:
                failures.append(f"{redact_url(url)}: error ({redact_text(str(exc))[:120]})")
                continue
            key = self._comparable(value)
            seen.setdefault(key, []).append(redact_url(url))
            raw_by_value.setdefault(key, value)
            if len(seen[key]) >= required:
                return raw_by_value[key]
        summary = "; ".join(f"{', '.join(urls)} -> {key[:40]}" for key, urls in seen.items())
        detail = summary or "no endpoint answered"
        if failures:
            detail += " | " + "; ".join(failures)
        raise QuorumError(f"quorum {required} not reached for {method}: {detail}")

    def eth_call_quorum(self, to: str, data: str, block: str = "latest") -> str:
        return self.request_quorum("eth_call", {"to": to, "data": data}, block)

    def refresh_endpoint_health(self, stale_seconds: int = 3600) -> list[dict[str, Any]]:
        """Probe each endpoint's latest block and mark stale providers."""
        stale: list[dict[str, Any]] = []
        now = int(time.time())
        for url in self.urls:
            client = Rpc(
                [url],
                timeout=self.timeout,
                retries=1,
                health=self.health,
                label=self.label,
                post=self._post_override,
            )
            try:
                number = int(client.request("eth_blockNumber"), 16)
                block = client.request("eth_getBlockByNumber", hex(number), False)
                timestamp = int(block.get("timestamp"), 16) if isinstance(block, dict) else None
                is_stale = bool(timestamp and now - timestamp > stale_seconds)
                self.health.mark_block(url, number, timestamp, stale=is_stale)
                if is_stale:
                    stale.append({
                        "url": redact_url(url),
                        "block_timestamp": timestamp,
                        "lag_seconds": now - timestamp,
                    })
            except (RpcError, TypeError, ValueError) as exc:
                self.health.failure(url, str(exc), label=self.label)
        return stale

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
        raise RpcError(f"batch failed: {redact_text(str(last))}")

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
    text = redact_text(str(exc) or exc.__class__.__name__)
    return text[:300]
