"""Alert scopes, escalation, resolution and opt-in webhook notifications."""

import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from idlescan.api import create_server
from idlescan.cli import main
from idlescan.config import Settings
from idlescan.db import Database
from idlescan.notifications import Notifier
from idlescan.selftest import MockRpc, build_world
from idlescan.service import PlatformService

WALLET = "0x" + "11" * 20
OTHER_WALLET = "0x" + "22" * 20
DROP = "demo-drop"
DISTRIBUTOR = "0x" + "dd" * 20


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def claim_payload(  # noqa: PLR0913 - test helper with many optional fields
    *,
    wallet=WALLET,
    status="unclaimed",
    net_usd=-1.0,
    deadline_days=10,
    proof_verified=True,
    drop_id=DROP,
):
    deadline = None
    if deadline_days is not None:
        deadline = (datetime.now(timezone.utc) + timedelta(days=deadline_days)).isoformat()
    return {
        "drop_id": drop_id,
        "name": "Demo Drop",
        "chain": "ethereum",
        "wallet": wallet,
        "distributor": DISTRIBUTOR,
        "status": status,
        "expired": False,
        "deadline": deadline,
        "amount_raw": str(10**18),
        "decimals": 18,
        "usd_price": 1.0,
        "usd_value": 1.0,
        "gas_usd": 2.0,
        "net_usd": net_usd,
        "proof_verified": proof_verified,
        "note": None,
    }


class AlertDatabaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "idlescan.db").init()

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, **overrides):
        values = dict(  # noqa: C408 - test helper, keyword args are intentional
            kind="deadline",
            severity="warning",
            title="Due",
            body="Soon",
            wallet=WALLET,
            drop_id=DROP,
        )
        values.update(overrides)
        return self.db.add_alert(**values)

    def test_add_alert_returns_the_new_id_or_none(self):
        first = self.add()
        self.assertIsInstance(first, int)
        self.assertIsNone(self.add())

    def test_dedupe_scope_includes_wallet_drop_kind_and_title(self):
        self.add()
        self.add(wallet=OTHER_WALLET)
        self.add(drop_id="another")
        self.add(kind="below_gas")
        self.add(title="Different title")
        self.assertEqual(len(self.db.list_alerts(include_ack=True)), 5)

    def test_tier_lets_a_condition_escalate(self):
        self.add(tier="14d")
        self.add(tier="3d")
        self.assertEqual(len(self.db.list_alerts()), 2)

    def test_resolve_hides_from_the_default_list_but_keeps_history(self):
        alert_id = self.add()
        self.assertTrue(self.db.resolve_alert(alert_id, "no longer true"))
        self.assertEqual(self.db.list_alerts(), [])
        history = self.db.list_alerts(include_resolved=True)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["resolved_reason"], "no longer true")
        self.assertIsNotNone(history[0]["resolved_at"])
        self.assertFalse(self.db.resolve_alert(alert_id))

    def test_reopen_clears_resolution_and_acknowledgement(self):
        alert_id = self.add()
        self.db.ack_alert(alert_id)
        self.db.resolve_alert(alert_id)
        self.assertTrue(self.db.reopen_alert(alert_id))
        opened = self.db.list_alerts()
        self.assertEqual(len(opened), 1)
        self.assertFalse(opened[0]["acknowledged"])
        self.assertIsNone(opened[0]["resolved_at"])
        self.assertFalse(self.db.reopen_alert(alert_id))

    def test_reopen_cannot_create_a_duplicate_open_alert(self):
        first = self.add()
        self.db.resolve_alert(first)
        self.add()  # a new alert with the same dedupe key is now allowed
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.reopen_alert(first)

    def test_stale_sweep_only_touches_named_kinds_and_drops(self):
        stale_same_drop = self.add(title="Old deadline")
        stale_other_drop = self.add(title="Other drop deadline", drop_id="other")
        event = self.add(kind="new_allocation", title="New allocation")
        active = self.add(title="Still true")
        changed = self.db.resolve_stale_alerts(
            {self.db.alert_key("deadline", "Still true", wallet=WALLET, drop_id=DROP)},
            kinds=("deadline", "below_gas"),
            drop_ids=[DROP],
        )
        self.assertEqual(changed, 1)
        open_rows = {row["id"] for row in self.db.list_alerts()}
        self.assertNotIn(stale_same_drop, open_rows)
        self.assertIn(stale_other_drop, open_rows)
        self.assertIn(event, open_rows)
        self.assertIn(active, open_rows)

    def test_notification_bookkeeping(self):
        alert_id = self.add()
        notification = self.db.add_notification(
            alert_id=alert_id, kind="deadline", url_redacted="https://hooks.example/x"
        )
        self.db.update_notification(notification, status="sent", attempts=2, error=None)
        rows = self.db.list_notifications()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "sent")
        self.assertEqual(rows[0]["attempts"], 2)
        self.assertEqual(rows[0]["url_redacted"], "https://hooks.example/x")
        self.assertEqual(rows[0]["dead_letter"], 0)


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "idlescan.db").init()

    def tearDown(self):
        self._tmp.cleanup()

    def alert(self, **overrides):
        values = {
            "id": 1,
            "kind": "deadline",
            "severity": "warning",
            "title": "Closing",
            "body": "10 days left",
            "wallet": WALLET,
            "drop_id": DROP,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        values.update(overrides)
        return values

    def notifier(self, sender, **overrides):
        values = dict(  # noqa: C408 - test helper, keyword args are intentional
            urls=["https://hooks.example/idlescan?token=SECRET"],
            db=self.db,
            sender=sender,
            sleep=lambda seconds: None,
            synchronous=True,
        )
        values.update(overrides)
        return Notifier(**values)

    def test_disabled_by_default(self):
        calls = []
        notifier = Notifier(sender=lambda *args: calls.append(args))
        self.assertFalse(notifier.enabled)
        notifier.dispatch([self.alert()])
        self.assertEqual(calls, [])

    def test_successful_delivery_is_recorded_with_a_redacted_url(self):
        calls = []

        def sender(url, body, timeout):
            calls.append((url, json.loads(body), timeout))
            return 200

        notifier = self.notifier(sender)
        notifier.dispatch([self.alert(severity="critical")])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["event"], "alert")
        self.assertEqual(calls[0][1]["alerts"][0]["title"], "Closing")
        self.assertEqual(calls[0][1]["source"], "idlescan")
        rows = self.db.list_notifications()
        self.assertEqual(rows[0]["status"], "sent")
        self.assertEqual(rows[0]["attempts"], 1)
        self.assertEqual(rows[0]["url_redacted"], "https://hooks.example/idlescan")

    def test_severity_and_kind_filters(self):
        calls = []
        notifier = self.notifier(
            lambda url, body, timeout: calls.append(body) or 200,
            min_severity="critical",
        )
        notifier.dispatch([self.alert(severity="warning")])
        notifier.dispatch([self.alert(severity="critical")])
        self.assertEqual(len(calls), 1)

        calls.clear()
        notifier = self.notifier(
            lambda url, body, timeout: calls.append(body) or 200,
            kinds=["below_gas"],
        )
        notifier.dispatch([self.alert(kind="deadline")])
        self.assertEqual(calls, [])

    def test_retries_then_succeeds(self):
        attempts = []

        def sender(url, body, timeout):
            attempts.append(body)
            if len(attempts) < 3:  # noqa: PLR2004 - test asserts exactly 3 attempts
                raise OSError("connection refused")  # noqa: TRY003
            return 204

        notifier = self.notifier(sender, max_attempts=3)
        notifier.dispatch([self.alert()])
        self.assertEqual(len(attempts), 3)
        row = self.db.list_notifications()[0]
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["attempts"], 3)
        self.assertIsNone(row["error"])

    def test_exhausted_retries_become_a_dead_letter_without_leaking_secrets(self):
        def sender(url, body, timeout):
            raise OSError(  # noqa: TRY003
                "failed posting to https://hooks.example/x?token=SECRET"
            )

        notifier = self.notifier(sender, max_attempts=2)
        notifier.dispatch([self.alert()])
        row = self.db.list_notifications()[0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(row["dead_letter"], 1)
        self.assertIn("OSError", row["error"])
        self.assertNotIn("SECRET", row["error"])

    def test_digest_sends_one_request_for_many_alerts(self):
        calls = []
        notifier = self.notifier(
            lambda url, body, timeout: calls.append(json.loads(body)) or 200,
            digest=True,
        )
        notifier.dispatch([self.alert(id=1), self.alert(id=2, title="Second", severity="critical")])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["event"], "digest")
        self.assertEqual(len(calls[0]["alerts"]), 2)

    def test_multiple_webhooks_get_every_alert(self):
        calls = []
        notifier = self.notifier(
            lambda url, body, timeout: calls.append(url) or 200,
            urls=["https://a.example/x", "https://b.example/y"],
        )
        notifier.dispatch([self.alert()])
        self.assertEqual(calls, ["https://a.example/x", "https://b.example/y"])

    def test_async_dispatch_does_not_block_on_a_slow_sender(self):
        release = threading.Event()

        def sender(url, body, timeout):
            release.wait(5)
            return 200

        notifier = self.notifier(sender, synchronous=False)
        started = time.monotonic()
        notifier.dispatch([self.alert()])
        self.assertLess(time.monotonic() - started, 1.0)
        release.set()
        deadline = time.monotonic() + 5
        while notifier.pending() and time.monotonic() < deadline:
            time.sleep(0.01)


class ScanAlertTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=Path(self._tmp.name) / "idlescan.db", demo=True)
        self.service = PlatformService(self.settings, rpc_factory=lambda chain: MockRpc(build_world()))
        self.service.notifier = Notifier()  # never touch the network in this suite

    def tearDown(self):
        self._tmp.cleanup()

    def generate(self, **claim_overrides):
        claim = claim_payload(**claim_overrides)
        payload = {
            "wallets": [claim["wallet"]],
            "chains": ["ethereum"],
            "claims": [claim],
            "holdings": [],
            "totals": {},
        }
        snapshot_id = self.service.db.save_snapshot(
            chains=["ethereum"],
            wallets=[claim["wallet"]],
            totals={},
            report=payload,
            trigger="test",
        )
        return self.service._generate_alerts(snapshot_id, payload)

    def titles(self, **kwargs):
        return [alert["title"] for alert in self.service.alerts(**kwargs)]

    def test_conditions_fire_once_and_do_not_duplicate_on_rescan(self):
        # below gas + 14-day deadline + the bundled registry's missing dataset
        self.assertEqual(self.generate(), 3)
        self.assertEqual(self.generate(), 0)
        titles = [row["title"] for row in self.service.alerts() if row["drop_id"] == DROP]
        self.assertEqual(
            sorted(titles),
            sorted([
                "Demo Drop costs more gas than it is worth",
                "Demo Drop claim window closing",
            ]),
        )

    def test_deadline_escalates_and_resolves_the_previous_tier(self):
        self.generate(deadline_days=10)
        self.assertEqual(self.generate(deadline_days=2), 1)
        open_alerts = self.service.alerts(include_resolved=True)
        deadline_rows = [row for row in open_alerts if row["kind"] == "deadline"]
        self.assertEqual(len(deadline_rows), 2)
        resolved = [row for row in deadline_rows if row["resolved_at"]]
        current = [row for row in deadline_rows if not row["resolved_at"]]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["tier"], "14d")
        self.assertEqual(current[0]["tier"], "3d")
        self.assertIn("3 days or less", current[0]["title"])

    def test_claiming_resolves_condition_alerts_and_records_an_event(self):
        self.generate()
        created = self.generate(status="claimed", net_usd=None, deadline_days=None)
        self.assertEqual(created, 1)
        open_alerts = [row for row in self.service.alerts() if row["drop_id"] == DROP]
        self.assertEqual([row["kind"] for row in open_alerts], ["claimed"])
        history = self.service.alerts(include_ack=True)
        resolved_kinds = {row["kind"] for row in history if row["resolved_at"]}
        self.assertEqual(resolved_kinds, {"below_gas", "deadline"})

    def test_resolution_is_scoped_to_drops_seen_by_the_scan(self):
        first = self.generate()
        self.assertTrue(first)
        other = claim_payload(drop_id="other-drop", wallet=OTHER_WALLET)
        payload = {
            "wallets": [OTHER_WALLET],
            "chains": ["ethereum"],
            "claims": [other],
            "holdings": [],
            "totals": {},
        }
        snapshot_id = self.service.db.save_snapshot(
            chains=["ethereum"],
            wallets=[OTHER_WALLET],
            totals={},
            report=payload,
            trigger="test",
        )
        self.service._generate_alerts(snapshot_id, payload)
        open_drop = [row for row in self.service.alerts() if row["drop_id"] == DROP]
        self.assertTrue(open_drop)  # a scan of another drop must not clear it

    def test_invalid_proof_alert_is_critical_and_clears_when_fixed(self):
        self.generate(proof_verified=False)
        proof = [row for row in self.service.alerts() if row["kind"] == "invalid_proof"]
        self.assertEqual(len(proof), 1)
        self.assertEqual(proof[0]["severity"], "critical")
        self.generate(proof_verified=True)
        self.assertFalse([row for row in self.service.alerts() if row["kind"] == "invalid_proof"])

    def test_new_alerts_are_dispatched_to_the_notifier(self):
        calls = []

        class Capture:
            def dispatch(self, alerts):
                calls.extend(alerts)

        self.service.notifier = Capture()
        self.generate()
        self.assertTrue(calls)
        self.assertIn("Demo Drop", calls[0]["title"])
        self.assertIn("id", calls[0])


class NotificationServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            db_path=Path(self._tmp.name) / "idlescan.db",
            demo=True,
            notify_webhook_urls=["https://hooks.example/idlescan"],
            notify_min_severity="warning",
        )
        self.service = PlatformService(self.settings, rpc_factory=lambda chain: MockRpc(build_world()))
        self.sent = []

        def sender(url, body, timeout):
            self.sent.append(json.loads(body))
            return 200

        self.service.notifier = Notifier(
            self.settings.notify_webhook_urls,
            db=self.service.db,
            min_severity="warning",
            sender=sender,
            synchronous=True,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_generated_alerts_reach_the_webhook_and_the_database(self):
        claim = claim_payload()
        payload = {
            "wallets": [WALLET],
            "chains": ["ethereum"],
            "claims": [claim],
            "holdings": [],
            "totals": {},
        }
        snapshot_id = self.service.db.save_snapshot(chains=["ethereum"], wallets=[WALLET], totals={}, report=payload)
        self.service._generate_alerts(snapshot_id, payload)
        # below_gas + deadline + missing dataset, one request per alert
        self.assertEqual(len(self.sent), 3)
        notifications = self.service.notifications()
        self.assertEqual(len(notifications), 3)
        self.assertTrue(all(row["status"] == "sent" for row in notifications))
        self.assertTrue(all(row["url_redacted"] == "https://hooks.example/idlescan" for row in notifications))


class AlertsApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(host="127.0.0.1", port=0, db_path=Path(self._tmp.name) / "api.db", demo=True)
        self.service = PlatformService(self.settings, rpc_factory=lambda chain: MockRpc(build_world()))
        self.service.notifier = Notifier()
        self.service.db.add_alert(
            kind="deadline",
            severity="critical",
            title="Closing",
            body="soon",
            wallet=WALLET,
            drop_id=DROP,
        )
        self.alert_id = self.service.db.list_alerts()[0]["id"]
        self.server = create_server(self.service, self.settings)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def post(self, path, body=None):
        data = json.dumps(body or {}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method="POST",
            headers={"content-type": "application/json", "x-idlescan": "1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=15) as response:
            return response.status, json.loads(response.read())

    def test_resolve_then_reopen(self):
        status, payload = self.post(f"/api/alerts/{self.alert_id}/resolve", {"reason": "fixed"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["resolved"])
        _, listing = self.get("/api/alerts")
        self.assertEqual(listing["alerts"], [])
        _, history = self.get("/api/alerts?all=1")
        self.assertEqual(history["alerts"][0]["resolved_reason"], "fixed")

        status, payload = self.post(f"/api/alerts/{self.alert_id}/reopen")
        self.assertEqual(status, 200)
        self.assertTrue(payload["reopened"])
        _, listing = self.get("/api/alerts")
        self.assertEqual(len(listing["alerts"]), 1)

    def test_resolve_unknown_alert_is_404(self):
        status, payload = self.post("/api/alerts/9999/resolve")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "not_found")

    def test_audit_exposes_notification_history(self):
        self.service.db.add_notification(
            alert_id=self.alert_id, kind="deadline", url_redacted="https://hooks.example/x"
        )
        _, payload = self.get("/api/audit")
        self.assertEqual(len(payload["notifications"]), 1)
        self.assertEqual(payload["notifications"][0]["status"], "pending")


class AlertsCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "idlescan.db"
        db = Database(self.path).init()
        self.alert_id = db.add_alert(
            kind="deadline",
            severity="critical",
            title="Closing",
            body="soon",
            wallet=WALLET,
            drop_id=DROP,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_resolve_and_reopen_from_the_cli(self):
        code, out, _ = run_cli("alerts", "--resolve", str(self.alert_id), "--db", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("resolved", out)
        code, out, _ = run_cli("alerts", "--db", str(self.path))
        self.assertIn("no open alerts", out)
        code, out, _ = run_cli("alerts", "--reopen", str(self.alert_id), "--db", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("reopened", out)

    def test_resolve_unknown_alert_exits_non_zero(self):
        code, _, err = run_cli("alerts", "--resolve", "9999", "--db", str(self.path))
        self.assertEqual(code, 1)
        self.assertIn("not found", err)


if __name__ == "__main__":
    unittest.main()
