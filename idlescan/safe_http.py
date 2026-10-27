"""One SSRF-safe path for every outbound non-RPC HTTP request.

Dataset URLs and webhook URLs are user-controlled, so the server must not be a
proxy into its own network.  This module resolves a host once, refuses every
non-global address, pins the connection to the validated IP while keeping the
Host header and TLS SNI on the original name, and re-validates each redirect.

RPC endpoints are operator-configured and do not go through this module.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

DEFAULT_CONTENT_TYPES = ("application/json", "text/plain")
MAX_REDIRECTS = 3
USER_AGENT = "idlescan/1.2 (+https://github.com/sang-4/idlescan)"


class FetchError(ValueError):
    """Base class for a refused or failed fetch."""


class BlockedURL(FetchError):
    """The URL points somewhere the server must not connect to."""


class FetchTooLarge(FetchError):
    """The response exceeded the configured size cap."""


class UnsupportedContentType(FetchError):
    """The response was not one of the allowed content types."""


@dataclass(frozen=True)
class FetchResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise FetchError(f"response is not valid JSON ({exc})") from exc


Resolver = Callable[[str, int], list[str]]
Transport = Callable[..., tuple[int, dict[str, str], bytes]]


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError(f"could not resolve {host}: {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _is_public(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global


def validate_url(
    url: str,
    *,
    allow_http: bool = False,
    allowed_hosts: Iterable[str] = (),
    resolver: Resolver | None = None,
    require_resolution: bool = True,
) -> str:
    """Validate a URL and return the first pinned IP address."""
    if not isinstance(url, str) or not url.strip():
        raise BlockedURL("URL is empty")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http"):
        raise BlockedURL(f"unsupported URL scheme {scheme or '(none)'!r}")
    if scheme == "http" and not allow_http:
        raise BlockedURL("only https URLs are allowed (set IDLESCAN_ALLOW_HTTP_FETCH=true for local fixtures)")
    if parts.username or parts.password:
        raise BlockedURL("URLs with embedded credentials are refused")
    host = (parts.hostname or "").lower()
    if not host:
        raise BlockedURL("URL has no host")
    if scheme == "https" and parts.port not in (None, 443):
        raise BlockedURL(f"port {parts.port} is not allowed for outbound https fetches")
    if scheme == "http" and not allow_http:
        raise BlockedURL("plain http is only allowed for explicit local fixtures")
    if scheme == "http" and parts.port == 443:
        raise BlockedURL("http on the https port is refused")

    allowed = [item.strip().lower() for item in allowed_hosts if item.strip()]
    if allowed:
        matched = False
        for entry in allowed:
            if entry.startswith("."):
                if host == entry[1:] or host.endswith(entry):
                    matched = True
            elif host == entry:
                matched = True
        if not matched:
            raise BlockedURL(f"host {host} is not in IDLESCAN_FETCH_ALLOWED_HOSTS")

    # A literal private IP never needs DNS.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not _is_public(str(literal)) and not allow_http:
        raise BlockedURL(f"{host} is a private, loopback or reserved address")

    if not require_resolution:
        return host
    port = parts.port or (443 if scheme == "https" else 80)
    addresses = (resolver or _resolve)(host, port)
    if not addresses:
        raise FetchError(f"no addresses found for {host}")
    if not allow_http:
        for address in addresses:
            if not _is_public(address):
                raise BlockedURL(f"{host} resolves to non-public address {address}; refusing to connect")
    return addresses[0]


def _content_type_ok(value: str | None, allowed: tuple[str, ...]) -> bool:
    if not value:
        return False
    mime = value.split(";", 1)[0].strip().lower()
    return mime in allowed


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ip: str, *, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, ip: str, *, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def _default_transport(
    url: str,
    ip: str,
    *,
    method: str,
    body: bytes | None,
    headers: Mapping[str, str],
    timeout: float,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    parts = urlsplit(url)
    https = parts.scheme.lower() == "https"
    port = parts.port or (443 if https else 80)
    connection: http.client.HTTPConnection
    if https:
        connection = _PinnedHTTPSConnection(parts.hostname or "", port, ip, timeout=timeout)
    else:
        connection = _PinnedHTTPConnection(parts.hostname or "", port, ip, timeout=timeout)
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    request_headers = {"user-agent": USER_AGENT, "accept": "*/*", **dict(headers)}
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        status = response.status
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        length = response_headers.get("content-length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise FetchTooLarge(f"response announces {length} bytes, cap is {max_bytes}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise FetchTooLarge(f"response exceeded the {max_bytes}-byte cap")
            chunks.append(chunk)
        return status, response_headers, b"".join(chunks)
    finally:
        connection.close()


def safe_fetch(
    url: str,
    *,
    method: str = "GET",
    body: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
    allow_http: bool = False,
    max_bytes: int = 25 * 1024 * 1024,
    allowed_hosts: Iterable[str] = (),
    timeout: float = 15.0,
    content_types: Iterable[str] | None = DEFAULT_CONTENT_TYPES,
    max_redirects: int = MAX_REDIRECTS,
    resolver: Resolver | None = None,
    transport: Transport | None = None,
) -> FetchResponse:
    """Fetch ``url`` through the SSRF guard, following at most three redirects."""
    if isinstance(body, str):
        body = body.encode()
    current = url
    current_method = method.upper()
    current_body = body
    allowed = tuple(item.lower() for item in (content_types or ()))
    for _ in range(max_redirects + 1):
        ip = validate_url(
            current,
            allow_http=allow_http,
            allowed_hosts=allowed_hosts,
            resolver=resolver,
        )
        status, response_headers, payload = (transport or _default_transport)(
            current,
            ip,
            method=current_method,
            body=current_body,
            headers=headers or {},
            timeout=timeout,
            max_bytes=max_bytes,
        )
        if len(payload) > max_bytes:
            raise FetchTooLarge(f"response exceeded the {max_bytes}-byte cap")
        if content_types is not None:
            if not _content_type_ok(response_headers.get("content-type"), allowed):
                raise UnsupportedContentType(
                    f"{current} returned {response_headers.get('content-type')!r}; allowed: {', '.join(allowed)}"
                )
        if status in (301, 302, 303, 307, 308):
            location = response_headers.get("location")
            if not location:
                raise FetchError(f"{current} redirected without a Location header")
            target = urljoin(current, location)
            if urlsplit(target).scheme.lower() != urlsplit(current).scheme.lower():
                raise BlockedURL("redirect changed scheme; refusing to follow")
            if status in (301, 302, 303):
                current_method, current_body = "GET", None
            current = target
            continue
        return FetchResponse(url=current, status=status, headers=response_headers, body=payload)
    raise FetchError(f"too many redirects (more than {max_redirects})")


def safe_fetch_json(
    url: str,
    *,
    allow_http: bool = False,
    max_bytes: int = 25 * 1024 * 1024,
    allowed_hosts: Iterable[str] = (),
    timeout: float = 15.0,
    resolver: Resolver | None = None,
    transport: Transport | None = None,
) -> Any:
    response = safe_fetch(
        url,
        allow_http=allow_http,
        max_bytes=max_bytes,
        allowed_hosts=allowed_hosts,
        timeout=timeout,
        resolver=resolver,
        transport=transport,
    )
    return response.json()


def redact_url(url: str) -> str:
    """Strip credentials, query strings and fragments for logs/audit."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[invalid url]"
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    path = parts.path or "/"
    return urlunsplit((parts.scheme, host + port, path, "", ""))
