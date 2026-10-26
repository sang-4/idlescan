"""Opt-in webhook notifications for new alerts.

Notifications are fire-and-forget (D-011): a scan must never wait for a webhook
or fail because one is down.  Every delivery is recorded in SQLite with a
redacted URL, attempt count and final status, so an operator can tell "the
webhook was never called" apart from "the webhook returned 500".

Disabled by default: with no ``IDLESCAN_NOTIFY_WEBHOOK_URLS`` the notifier is a
no-op and no thread is started.  Outbound requests go through
:mod:`idlescan.safe_http`, so a webhook cannot become an SSRF pivot.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from . import __version__
from .config import Settings
from .safe_http import FetchError, redact_url, safe_fetch

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
MAX_ATTEMPTS = 3
MAX_WEBHOOKS = 10
MAX_RESPONSE_BYTES = 64 * 1024
RETRY_BASE_SECONDS = 0.5
WEBHOOK_ERROR_STATUS = 300


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Notifier:
    """Deliver new alerts to operator webhooks, with bounded retries."""

    def __init__(  # noqa: PLR0913 - configuration object with many optional knobs
        self,
        urls: Iterable[str] = (),
        *,
        db: Any = None,
        min_severity: str = "warning",
        kinds: Iterable[str] = (),
        digest: bool = False,
        allow_http: bool = False,
        timeout: float = 5.0,
        max_attempts: int = MAX_ATTEMPTS,
        sender: Callable[[str, bytes, float], int] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        synchronous: bool = False,
    ) -> None:
        self.urls = [str(url).strip() for url in urls if str(url).strip()][:MAX_WEBHOOKS]
        self.db = db
        self.min_severity = min_severity if min_severity in SEVERITY_ORDER else "warning"
        self.kinds = {str(kind) for kind in kinds}
        self.digest = digest
        self.allow_http = allow_http
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self._sender = sender
        self._sleep = sleep
        self.synchronous = synchronous
        self._lock = threading.Lock()
        self._pending = 0

    @classmethod
    def from_settings(cls, settings: Settings, *, db: Any = None) -> Notifier:
        return cls(
            settings.notify_webhook_urls,
            db=db,
            min_severity=settings.notify_min_severity,
            kinds=settings.notify_kinds,
            digest=settings.notify_digest,
            allow_http=settings.allow_http_fetch,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.urls)

    def wanted(self, alert: dict[str, Any]) -> bool:
        threshold = SEVERITY_ORDER.get(str(alert.get("severity", "info")), 0)
        if threshold < SEVERITY_ORDER[self.min_severity]:
            return False
        return not self.kinds or str(alert.get("kind", "")) in self.kinds

    def dispatch(self, alerts: Iterable[dict[str, Any]]) -> None:
        """Queue new alerts without blocking the caller."""
        selected = [alert for alert in alerts if self.wanted(alert)]
        if not selected or not self.enabled:
            return
        if self.synchronous:
            self.deliver(selected)
            return
        thread = threading.Thread(
            target=self.deliver,
            args=(selected,),
            name="idlescan-notify",
            daemon=True,
        )
        thread.start()

    def deliver(self, alerts: list[dict[str, Any]]) -> int:
        """Send alerts now; returns the number of successful webhook calls."""
        units: list[dict[str, Any]]
        if self.digest:
            units = [{"event": "digest", "alerts": alerts}]
        else:
            units = [{"event": "alert", "alerts": [alert]} for alert in alerts]
        delivered = 0
        for url in self.urls:
            for unit in units:
                if self._deliver_one(url, unit):
                    delivered += 1
        return delivered

    # ------------------------------------------------------------- internals
    def _payload(self, unit: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": "idlescan",
            "version": __version__,
            "sent_at": _now(),
            "event": unit["event"],
            "alerts": [
                {
                    "id": alert.get("id"),
                    "kind": alert.get("kind"),
                    "severity": alert.get("severity"),
                    "title": alert.get("title"),
                    "body": alert.get("body"),
                    "wallet": alert.get("wallet"),
                    "drop_id": alert.get("drop_id"),
                    "created_at": alert.get("created_at"),
                }
                for alert in unit["alerts"]
            ],
        }

    def _send(self, url: str, body: bytes) -> int:
        if self._sender is not None:
            return int(self._sender(url, body, self.timeout))
        response = safe_fetch(
            url,
            method="POST",
            body=body,
            headers={
                "content-type": "application/json",
                "user-agent": f"idlescan/{__version__}",
            },
            allow_http=self.allow_http,
            max_bytes=MAX_RESPONSE_BYTES,
            timeout=self.timeout,
            content_types=None,
        )
        if response.status >= WEBHOOK_ERROR_STATUS:
            raise FetchError(f"webhook returned HTTP {response.status}")  # noqa: TRY003
        return response.status

    def _deliver_one(self, url: str, unit: dict[str, Any]) -> bool:
        payload = json.dumps(self._payload(unit), default=str).encode()
        first = unit["alerts"][0] if unit["alerts"] else {}
        notification_id = None
        if self.db is not None:
            try:
                notification_id = self.db.add_notification(
                    alert_id=first.get("id"),
                    kind=str(first.get("kind") or unit["event"]),
                    url_redacted=redact_url(url),
                )
            except Exception:  # delivery must not break a scan
                notification_id = None
        with self._lock:
            self._pending += 1
        try:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    self._send(url, payload)
                except Exception as exc:  # every failure is recorded
                    final = attempt >= self.max_attempts
                    error = f"{exc.__class__.__name__}: {exc}"
                    if notification_id is not None:
                        self._safe_update(
                            notification_id,
                            status="failed" if final else "retrying",
                            attempts=attempt,
                            error=error,
                            dead_letter=final,
                        )
                    if final:
                        return False
                    self._sleep(min(RETRY_BASE_SECONDS * (2 ** (attempt - 1)), 5.0))
                    continue
                if notification_id is not None:
                    self._safe_update(notification_id, status="sent", attempts=attempt, error=None)
                return True
            return False
        finally:
            with self._lock:
                self._pending = max(0, self._pending - 1)

    def _safe_update(self, notification_id: int, **fields: Any) -> None:
        with contextlib.suppress(Exception):
            self.db.update_notification(notification_id, **fields)

    def pending(self) -> int:
        with self._lock:
            return self._pending
