"""HTTP API + static frontend, on the standard library only.

Design notes
------------
* ``ThreadingHTTPServer`` so a scan can run while the UI polls for progress.
* Handlers are thin: they validate input, call :class:`PlatformService`, and map
  errors to JSON. All business rules (including the read-only gate) live in the
  service layer, so the CLI cannot bypass them either.
* State-changing requests require the ``X-Idlescan: 1`` header and, when an
  ``Origin`` header is present, a matching host. That blocks cross-site form
  posts from a random page against a locally served instance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Settings
from .service import Conflict, NotFound, PlatformService, ServiceError, WriteDisabled

WEB_ROOT = Path(__file__).resolve().parent / "web"
MAX_BODY = 1_000_000

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "content-security-policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern, Callable]] = []

    def add(self, method: str, pattern: str, handler: Callable) -> None:
        self.routes.append((method, re.compile(f"^{pattern}$"), handler))

    def match(self, method: str, path: str):
        allowed = False
        for route_method, pattern, handler in self.routes:
            match = pattern.match(path)
            if not match:
                continue
            if route_method != method:
                allowed = True
                continue
            return handler, match.groupdict()
        if allowed:
            raise ServiceError(f"method {method} not allowed on {path}")
        raise NotFound(f"no route for {method} {path}")


class ApiHandler(BaseHTTPRequestHandler):
    server_version = f"idlescan/{__version__}"
    router: Router
    service: PlatformService
    settings: Settings
    warnings: list[str]

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if os.environ.get("IDLESCAN_HTTP_LOG") == "1":
            super().log_message(fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        self._head_only = method == "HEAD"
        try:
            if method in ("GET", "HEAD") and not path.startswith("/api/"):
                self._serve_static(path, head=method == "HEAD")
                return
            if method in ("POST", "PATCH", "DELETE"):
                self._check_origin()
            handler, params = self.router.match("GET" if method == "HEAD" else method, path)
            body = self._read_json() if method in ("POST", "PATCH") else {}
            result = handler(self, params=params, query=query, body=body)
            if result is not None:
                self._send_json(200, result)
        except WriteDisabled as exc:
            self._send_json(403, {"error": str(exc), "code": "read_only"})
        except NotFound as exc:
            self._send_json(404, {"error": str(exc), "code": "not_found"})
        except Conflict as exc:
            self._send_json(409, {"error": str(exc), "code": "conflict"})
        except ServiceError as exc:
            self._send_json(getattr(exc, "status", 400), {"error": str(exc)})
        except BrokenPipeError:
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": f"{exc.__class__.__name__}: {exc}"})

    def _read_json(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            raise ServiceError("request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ServiceError(f"invalid JSON body: {exc}") from None
        if not isinstance(payload, dict):
            raise ServiceError("JSON body must be an object")
        return payload

    def _check_origin(self) -> None:
        if self.headers.get("x-idlescan") != "1":
            raise ServiceError(
                "missing X-Idlescan header; state-changing requests must come from the app"
            )
        origin = self.headers.get("origin")
        if origin:
            host = urlparse(origin).netloc
            if host and host != self.headers.get("host"):
                raise ServiceError("cross-origin request rejected")

    def _security_headers(self) -> None:
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self._security_headers()
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _send_download(
        self,
        payload: bytes,
        content_type: str,
        *,
        filename: str | None = None,
        cache_control: str = "no-store",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(200)
        self.send_header("content-type", content_type)
        if filename:
            self.send_header("content-disposition", f'attachment; filename="{filename}"')
        self.send_header("content-length", str(len(payload)))
        self.send_header("cache-control", cache_control)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self._security_headers()
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            pass

    def _serve_static(self, path: str, *, head: bool) -> None:
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if not target.is_relative_to(WEB_ROOT.resolve()) or not target.is_file():
            target = WEB_ROOT / "index.html"
        if not target.is_file():
            self._send_json(500, {"error": "frontend assets missing"})
            return
        data = target.read_bytes()
        etag = f'"{hashlib.sha256(data).hexdigest()[:32]}"'
        if_none_match = self.headers.get("if-none-match") or ""
        if etag in [item.strip() for item in if_none_match.split(",")]:
            self.send_response(304)
            self.send_header("etag", etag)
            self.send_header("cache-control", "no-cache" if target.suffix == ".html" else "max-age=300")
            self._security_headers()
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("content-length", str(len(data)))
        self.send_header("cache-control", "no-cache" if target.suffix == ".html" else "max-age=300")
        self.send_header("etag", etag)
        self._security_headers()
        self.end_headers()
        if not head:
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass


# ------------------------------------------------------------------ handlers
def _svc(handler: ApiHandler) -> PlatformService:
    return handler.service


def health(handler, params, query, body):
    return {"status": "ok", "version": __version__, "read_only": handler.settings.read_only}


def config(handler, params, query, body):
    settings = handler.settings
    return {
        "version": __version__,
        "read_only": settings.read_only,
        "write_enabled": settings.write_enabled,
        "demo": settings.demo,
        "host": settings.host,
        "port": settings.port,
        "refresh_minutes": settings.refresh_minutes,
        "warnings": handler.warnings,
        "chains": [
            {
                "key": chain.key,
                "name": chain.name,
                "chain_id": chain.chain_id,
                "native": chain.native.symbol,
                "explorer": chain.explorer,
            }
            for chain in sorted(handler.service.chains.chains.values(), key=lambda c: c.chain_id)
        ],
    }


def _int_query(query: dict, key: str, *, default: int | None = None) -> int | None:
    values = query.get(key)
    if not values:
        return default
    raw = str(values[0]).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ServiceError(f"query parameter {key!r} must be an integer") from None


def _q(query: dict, key: str, default: str = "") -> str:
    return str((query.get(key) or [default])[0] or "").strip()


def stats(handler, params, query, body):
    return _svc(handler).stats()


def wallets_list(handler, params, query, body):
    return {"wallets": _svc(handler).list_wallets()}


def wallets_add(handler, params, query, body):
    wallet = _svc(handler).add_wallet(
        str(body.get("address", "")), str(body.get("label", "")), str(body.get("tags", ""))
    )
    return {"wallet": wallet}


def wallets_bulk(handler, params, query, body):
    return _svc(handler).add_wallets(body.get("wallets", []))


def wallets_update(handler, params, query, body):
    wallet = _svc(handler).add_wallet(
        params["address"], str(body.get("label", "")), str(body.get("tags", ""))
    )
    return {"wallet": wallet}


def wallets_delete(handler, params, query, body):
    if not _svc(handler).delete_wallet(params["address"]):
        raise NotFound("wallet not found")
    return {"removed": True}


def airdrops_list(handler, params, query, body):
    return {"airdrops": _svc(handler).airdrops()}


def airdrops_add(handler, params, query, body):
    return {"airdrop": _svc(handler).add_airdrop(body)}


def airdrops_update(handler, params, query, body):
    enabled = bool(body.get("enabled", True))
    _svc(handler).set_airdrop_enabled(params["airdrop_id"], enabled)
    return {"updated": True, "enabled": enabled}


def airdrops_delete(handler, params, query, body):
    _svc(handler).delete_airdrop(params["airdrop_id"])
    return {"removed": True}


def airdrop_detail(handler, params, query, body):
    return _svc(handler).airdrop_detail(params["airdrop_id"])


def airdrop_export(handler, params, query, body):
    filename, content_type, payload = _svc(handler).export_drop(params["airdrop_id"], params["fmt"])
    handler._send_download(payload, content_type, filename=filename)
    return None


def wallet_detail(handler, params, query, body):
    return _svc(handler).wallet_detail(params["address"])


def search(handler, params, query, body):
    limit = _int_query(query, "limit", default=6)
    if limit is None or limit < 1:
        limit = 6
    if limit > 25:
        raise ServiceError("query parameter 'limit' must be 25 or less")
    return _svc(handler).search(_q(query, "q"), limit=limit)


def analytics(handler, params, query, body):
    return _svc(handler).analytics()


def check_address(handler, params, query, body):
    return _svc(handler).check_address(str(body.get("address", "")))


def snapshot_diff(handler, params, query, body):
    return _svc(handler).snapshot_diff(int(params["snapshot_id"]))


def calendar(handler, params, query, body):
    drop_id = _q(query, "drop") or None
    payload = _svc(handler).calendar_ics(drop_id)
    handler._send_download(
        payload,
        "text/calendar; charset=utf-8",
        filename="idlescan-deadlines.ics",
        cache_control="no-cache",
    )
    return None


def backup(handler, params, query, body):
    path = _svc(handler).backup()
    return {"path": str(path), "size": path.stat().st_size}


def scan_start(handler, params, query, body):
    chains = body.get("chains") or handler.settings.chains or None
    job = _svc(handler).start_scan(chains, trigger="ui")
    return {"job": job}


def jobs_list(handler, params, query, body):
    return {"jobs": _svc(handler).db.recent_jobs(20)}


def job_get(handler, params, query, body):
    return {"job": _svc(handler).job(params["job_id"])}


def portfolio(handler, params, query, body):
    snapshot_id = _int_query(query, "snapshot")
    return _svc(handler).portfolio(snapshot_id)


def snapshots(handler, params, query, body):
    return {"snapshots": _svc(handler).snapshots()}


def snapshot_get(handler, params, query, body):
    payload = _svc(handler).portfolio(int(params["snapshot_id"]))
    if not payload.get("snapshot"):
        raise NotFound("snapshot not found")
    return payload


def alerts_list(handler, params, query, body):
    include = query.get("all", ["0"])[0] in ("1", "true", "yes")
    return {"alerts": _svc(handler).alerts(include_ack=include)}


def alert_ack(handler, params, query, body):
    if not _svc(handler).ack_alert(int(params["alert_id"])):
        raise NotFound("alert not found")
    return {"acknowledged": True}


def alerts_ack_all(handler, params, query, body):
    return {"acknowledged": _svc(handler).ack_all_alerts()}


def audit(handler, params, query, body):
    return {"audit": _svc(handler).audit(), "executions": _svc(handler).executions()}


def claim_prepare(handler, params, query, body):
    return _svc(handler).prepare_claim(params["drop_id"], params["wallet"])


def claim_confirm(handler, params, query, body):
    return _svc(handler).confirm_claim(
        drop_id=str(body.get("drop_id", "")),
        wallet=str(body.get("wallet", "")),
        tx_hash=str(body.get("tx_hash", "")),
    )


def export(handler, params, query, body):
    fmt = params["fmt"]
    filename, content_type, payload = _svc(handler).export(fmt)
    handler._send_download(payload, content_type, filename=filename)
    return None


def build_router() -> Router:
    router = Router()
    router.add("GET", r"/api/health", health)
    router.add("GET", r"/api/config", config)
    router.add("GET", r"/api/stats", stats)
    router.add("GET", r"/api/wallets", wallets_list)
    router.add("POST", r"/api/wallets/bulk", wallets_bulk)
    router.add("GET", r"/api/wallets/(?P<address>0x[0-9a-fA-F]{40})", wallet_detail)
    router.add("POST", r"/api/wallets", wallets_add)
    router.add("PATCH", r"/api/wallets/(?P<address>0x[0-9a-fA-F]{40})", wallets_update)
    router.add("DELETE", r"/api/wallets/(?P<address>0x[0-9a-fA-F]{40})", wallets_delete)
    router.add("GET", r"/api/airdrops", airdrops_list)
    router.add("GET", r"/api/airdrops/(?P<airdrop_id>[\w.\-]+)/export/(?P<fmt>json|csv|md)", airdrop_export)
    router.add("GET", r"/api/airdrops/(?P<airdrop_id>[\w.\-]+)", airdrop_detail)
    router.add("POST", r"/api/airdrops", airdrops_add)
    router.add("PATCH", r"/api/airdrops/(?P<airdrop_id>[\w.\-]+)", airdrops_update)
    router.add("DELETE", r"/api/airdrops/(?P<airdrop_id>[\w.\-]+)", airdrops_delete)
    router.add("POST", r"/api/scan", scan_start)
    router.add("GET", r"/api/jobs", jobs_list)
    router.add("GET", r"/api/jobs/(?P<job_id>[\w\-]+)", job_get)
    router.add("GET", r"/api/search", search)
    router.add("GET", r"/api/analytics", analytics)
    router.add("POST", r"/api/check", check_address)
    router.add("GET", r"/api/portfolio", portfolio)
    router.add("GET", r"/api/snapshots", snapshots)
    router.add("GET", r"/api/snapshots/(?P<snapshot_id>\d+)/diff", snapshot_diff)
    router.add("GET", r"/api/snapshots/(?P<snapshot_id>\d+)", snapshot_get)
    router.add("GET", r"/api/alerts", alerts_list)
    router.add("POST", r"/api/alerts/(?P<alert_id>\d+)/ack", alert_ack)
    router.add("POST", r"/api/alerts/ack-all", alerts_ack_all)
    router.add("GET", r"/api/audit", audit)
    router.add("POST", r"/api/claims/(?P<drop_id>[\w.\-]+)/(?P<wallet>0x[0-9a-fA-F]{40})/prepare", claim_prepare)
    router.add("POST", r"/api/claims/confirm", claim_confirm)
    router.add("GET", r"/api/export/(?P<fmt>json|csv|md)", export)
    router.add("GET", r"/api/calendar\.ics", calendar)
    router.add("POST", r"/api/backup", backup)
    return router


def create_server(
    service: PlatformService,
    settings: Settings,
    *,
    warnings: list[str] | None = None,
    port: int | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundApiHandler",
        (ApiHandler,),
        {
            "router": build_router(),
            "service": service,
            "settings": settings,
            "warnings": warnings or [],
        },
    )
    bind_port = settings.port if port is None else port
    try:
        server = ThreadingHTTPServer((settings.host, bind_port), handler)
    except OSError as exc:
        raise ServiceError(f"cannot bind {settings.host}:{bind_port}: {exc}") from None
    server.daemon_threads = True
    server.service = service  # type: ignore[attr-defined]
    return server


def _refresh_loop(service: PlatformService, minutes: int, stop: threading.Event) -> None:
    while not stop.wait(minutes * 60):
        try:
            service.start_scan(None, trigger="schedule")
        except Conflict:
            pass
        except Exception as exc:  # noqa: BLE001
            print(f"[refresh] scan failed: {exc}", file=sys.stderr)


def run(settings: Settings, *, service: PlatformService | None = None) -> int:
    warnings = settings.validate()
    service = service or PlatformService(settings)
    if settings.demo:
        service.seed_demo()
    server = create_server(service, settings, warnings=warnings)
    host, port = server.server_address[0], server.server_address[1]
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host

    print(f"idlescan {__version__}  ·  http://{url_host}:{port}")
    printer = "read-only" if settings.read_only else "WRITE MODE ENABLED"
    print(f"mode: {printer}   db: {settings.db_path}")
    if settings.env_file:
        print(f"env:  {settings.env_file}")
    for warning in warnings:
        print(f"warning: {warning}")
    print("press ctrl-c to stop")

    stop = threading.Event()

    def _handle_term(signum, frame):  # noqa: ARG001
        if stop.is_set():
            return
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, _handle_term)
    except (ValueError, OSError):
        pass

    refresh = None
    if settings.refresh_minutes > 0:
        refresh = threading.Thread(
            target=_refresh_loop, args=(service, settings.refresh_minutes, stop), daemon=True
        )
        refresh.start()
        print(f"auto-refresh: every {settings.refresh_minutes} minute(s)")
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
    return 0


def free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]
