"""HTTP API + static frontend: routing, CSRF guard, read-only gate, write hand-off."""

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from idlescan.api import create_server
from idlescan.config import Settings
from idlescan.selftest import WALLET_CLAIMED, WALLET_UNCLAIMED, MockRpc, build_world
from idlescan.service import PlatformService

TX = "0x" + "cd" * 32


class ApiTestCase(unittest.TestCase):
    read_only = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            read_only=self.read_only,
            host="127.0.0.1",
            port=0,
            db_path=Path(self._tmp.name) / "api.db",
            demo=True,
        )
        self.service = PlatformService(
            self.settings,
            rpc_factory=lambda chain: MockRpc(build_world()),
        )
        self.service.seed_demo()
        self.server = create_server(self.service, self.settings, warnings=self.settings.validate())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def http(self, path, method="GET", body=None, headers=None):
        data = None
        request_headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            request_headers.setdefault("content-type", "application/json")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            response_headers = {key.lower(): value for key, value in exc.headers.items()}
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            payload = raw
        return status, payload, response_headers

    def json(self, path, method="GET", body=None, headers=None):
        status, payload, _ = self.http(path, method, body, headers)
        return status, payload


class ReadOnlyApiTests(ApiTestCase):
    def test_health_and_config(self):
        status, payload = self.json("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["read_only"])

        status, payload = self.json("/api/config")
        self.assertEqual(status, 200)
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["write_enabled"])
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["port"], 0)
        self.assertEqual(len(payload["chains"]), 14)

    def test_stats(self):
        status, payload = self.json("/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(payload["wallets"], 3)
        self.assertIsNotNone(payload["snapshot"])
        self.assertEqual(payload["snapshot"]["unclaimed_count"], 1)

    def test_static_frontend_is_served(self):
        status, payload, headers = self.http("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["content-type"])
        self.assertIn(b"idlescan", payload)
        self.assertIn(b"app.js", payload)

        status, payload, headers = self.http("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["content-type"])
        self.assertIn(b"prepareClaim", payload)

        status, payload, headers = self.http("/app.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["content-type"])

        status, _, headers = self.http("/favicon.svg")
        self.assertEqual(status, 200)
        self.assertIn("svg", headers["content-type"])

    def test_unknown_api_route_is_404(self):
        status, payload = self.json("/api/does-not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "not_found")

    def test_spa_fallback_for_unknown_path(self):
        status, payload, headers = self.http("/claims")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["content-type"])
        self.assertIn(b"<div class=\"shell\">", payload)

    def test_wallets_crud(self):
        status, payload = self.json("/api/wallets")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["wallets"]), 3)

        status, payload = self.json("/api/wallets", "POST", {"address": "0x" + "ab" * 20}, {"X-Idlescan": "1"})
        self.assertEqual(status, 200)
        address = payload["wallet"]["address"]

        status, payload = self.json(
            f"/api/wallets/{address}", "PATCH", {"label": "API wallet", "tags": "api"}, {"X-Idlescan": "1"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["wallet"]["label"], "API wallet")

        status, payload = self.json(f"/api/wallets/{address}", "DELETE", {}, {"X-Idlescan": "1"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["removed"])

        status, payload = self.json("/api/wallets", "POST", {"address": "nope"}, {"X-Idlescan": "1"})
        self.assertEqual(status, 400)

    def test_airdrops_list_and_custom_lifecycle(self):
        status, payload = self.json("/api/airdrops")
        self.assertEqual(status, 200)
        ids = {drop["id"] for drop in payload["airdrops"]}
        self.assertIn("demo-merkle", ids)
        self.assertIn("uniswap-uni", ids)

        custom = {
            "id": "api-drop",
            "name": "API Drop",
            "chain": "ethereum",
            "distributor": "0x" + "ee" * 20,
            "token": "0x" + "ff" * 20,
            "links": {"docs": "https://example.com"},
        }
        status, payload = self.json("/api/airdrops", "POST", custom, {"X-Idlescan": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["airdrop"]["source"], "custom")

        status, payload = self.json(
            "/api/airdrops/api-drop", "PATCH", {"enabled": False}, {"X-Idlescan": "1"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])

        status, _ = self.json("/api/airdrops/api-drop", "DELETE", {}, {"X-Idlescan": "1"})
        self.assertEqual(status, 200)

        status, _ = self.json("/api/airdrops/uniswap-uni", "DELETE", {}, {"X-Idlescan": "1"})
        self.assertEqual(status, 409)

    def test_state_changing_requests_need_the_header(self):
        status, payload = self.json("/api/scan", "POST", {})
        self.assertEqual(status, 400)
        self.assertIn("X-Idlescan", payload["error"])

        status, payload = self.json(
            "/api/scan", "POST", {}, {"X-Idlescan": "1", "Origin": "http://evil.example"}
        )
        self.assertEqual(status, 400)
        self.assertIn("cross-origin", payload["error"])

    def test_read_only_prepare_is_refused(self):
        status, payload = self.json(
            f"/api/claims/demo-merkle/{WALLET_UNCLAIMED}/prepare",
            "POST",
            {},
            {"X-Idlescan": "1"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["code"], "read_only")
        self.assertIn("IDLESCAN_READ_ONLY=false", payload["error"])
        self.assertEqual(self.service.executions(), [])

    def test_scan_job_completes_offline(self):
        status, payload = self.json("/api/scan", "POST", {"chains": ["ethereum"]}, {"X-Idlescan": "1"})
        self.assertEqual(status, 200)
        job_id = payload["job"]["id"]
        deadline = time.time() + 10
        job = None
        while time.time() < deadline:
            status, payload = self.json(f"/api/jobs/{job_id}")
            job = payload["job"]
            if job["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(job["result"]["claims"], 3)
        self.assertEqual(job["result"]["holdings"], 4)

        status, payload = self.json("/api/snapshots")
        self.assertGreaterEqual(len(payload["snapshots"]), 2)

    def test_alerts_and_acknowledgement(self):
        status, payload = self.json("/api/alerts")
        self.assertEqual(status, 200)
        self.assertTrue(payload["alerts"])
        alert_id = payload["alerts"][0]["id"]

        status, payload = self.json(f"/api/alerts/{alert_id}/ack", "POST", {}, {"X-Idlescan": "1"})
        self.assertEqual(status, 200)

        status, payload = self.json("/api/alerts?all=1")
        self.assertGreaterEqual(len(payload["alerts"]), 1)

        status, payload = self.json("/api/alerts/ack-all", "POST", {}, {"X-Idlescan": "1"})
        self.assertEqual(status, 200)
        status, payload = self.json("/api/alerts")
        self.assertEqual(payload["alerts"], [])

    def test_portfolio_snapshots_and_audit(self):
        status, payload = self.json("/api/portfolio")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["claims"]), 3)
        unclaimed = next(row for row in payload["claims"] if row["status"] == "unclaimed")
        self.assertEqual(unclaimed["wallet_label"], "Demo cold wallet")

        snapshot_id = payload["snapshot"]["id"]
        status, payload = self.json(f"/api/snapshots/{snapshot_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["snapshot"]["id"], snapshot_id)

        status, payload = self.json("/api/snapshots/99999")
        self.assertEqual(status, 404)

        status, payload = self.json("/api/audit")
        self.assertEqual(status, 200)
        self.assertIn("audit", payload)
        self.assertIn("executions", payload)

    def test_exports(self):
        status, payload, headers = self.http("/api/export/json")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("content-disposition", ""))
        self.assertEqual(payload["claims"][0]["drop_id"], "demo-merkle")

        for fmt, needle in (("csv", b"Demo Airdrop"), ("md", b"Idle asset")):
            status, payload, headers = self.http(f"/api/export/{fmt}")
            self.assertEqual(status, 200)
            self.assertIn("attachment", headers.get("content-disposition", ""))
            self.assertIn(needle, payload)

        status, payload = self.json("/api/export/xml")
        self.assertEqual(status, 404)

    def test_security_headers_and_etag(self):
        status, payload, headers = self.http("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("content-security-policy", headers)
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["referrer-policy"], "no-referrer")

        status, payload, headers = self.http("/app.css")
        self.assertEqual(status, 200)
        etag = headers.get("etag")
        self.assertTrue(etag)
        self.assertIn("content-security-policy", headers)

        status, payload, headers = self.http("/app.css", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(headers.get("etag"), etag)
        self.assertEqual(payload, b"")

    def test_airdrop_and_wallet_profiles(self):
        status, payload = self.json("/api/airdrops/demo-merkle")
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "demo-merkle")
        self.assertIn("contract", payload)
        self.assertTrue(payload["contract"]["distributor_url"].startswith("https://"))
        self.assertEqual(len(payload["positions"]), 3)
        self.assertIn("history", payload)
        self.assertIn("csv", payload["export"])

        status, payload = self.json("/api/airdrops/nope")
        self.assertEqual(status, 404)

        status, payload = self.json(f"/api/wallets/{WALLET_UNCLAIMED}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["wallet"]["address"], WALLET_UNCLAIMED)
        self.assertEqual(payload["totals"]["unclaimed"], 1)
        self.assertTrue(payload["claims"])
        self.assertIn("history", payload)

        status, payload = self.json("/api/wallets/0x" + "99" * 20)
        self.assertEqual(status, 404)

        status, payload = self.json("/api/wallets/not-an-address")
        self.assertEqual(status, 404)

    def test_search_analytics_and_diff(self):
        status, payload = self.json("/api/search?q=demo")
        self.assertEqual(status, 200)
        self.assertTrue(payload["drops"])
        self.assertEqual(payload["drops"][0]["id"], "demo-merkle")

        status, payload = self.json("/api/search?q=" + WALLET_UNCLAIMED[:10])
        self.assertEqual(status, 200)
        self.assertTrue(payload["wallets"])

        status, payload = self.json("/api/search?q=demo&limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["drops"]), 1)

        status, payload = self.json("/api/analytics")
        self.assertEqual(status, 200)
        self.assertIn("history", payload)
        self.assertIn("chain_breakdown", payload)
        self.assertEqual(payload["totals"]["positions"], 3)
        self.assertIsNotNone(payload["snapshot"])

        snapshot_id = payload["snapshot"]["id"]
        status, payload = self.json(f"/api/snapshots/{snapshot_id}/diff")
        self.assertEqual(status, 200)
        self.assertIsNone(payload["previous"])
        self.assertEqual(payload["changes"], [])

        self.service.run_scan_sync(wallets=[WALLET_UNCLAIMED], trigger="test")
        latest = self.service.db.latest_snapshot()["id"]
        status, payload = self.json(f"/api/snapshots/{latest}/diff")
        self.assertEqual(status, 200)
        self.assertEqual(payload["previous"]["id"], snapshot_id)

        status, payload = self.json("/api/snapshots/99999/diff")
        self.assertEqual(status, 404)

    def test_check_address_and_bulk_import(self):
        status, payload = self.json(
            "/api/check", "POST", {"address": WALLET_UNCLAIMED}, {"X-Idlescan": "1"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["address"], WALLET_UNCLAIMED)
        self.assertEqual(payload["totals"]["unclaimed"], 1)
        self.assertTrue(payload["tracked"])
        self.assertEqual(len(payload["claims"]), 1)

        status, payload = self.json("/api/check", "POST", {"address": "nope"}, {"X-Idlescan": "1"})
        self.assertEqual(status, 400)

        status, payload = self.json("/api/check", "POST", {"address": WALLET_UNCLAIMED})
        self.assertEqual(status, 400)

        status, payload = self.json(
            "/api/wallets/bulk",
            "POST",
            {"wallets": [{"address": "0x" + "AB" * 20, "label": "Bulk"}, {"address": "bad"}]},
            {"X-Idlescan": "1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["added"], 1)
        self.assertEqual(len(payload["errors"]), 1)

        status, payload = self.json("/api/config")
        self.assertEqual(status, 200)
        self.assertNotIn("db_path", payload)
        self.assertNotIn("env_file", payload)

    def test_invalid_query_parameters_are_400(self):
        for path in ("/api/portfolio?snapshot=abc", "/api/search?limit=100", "/api/search?limit=x"):
            status, payload = self.json(path)
            self.assertEqual(status, 400, path)

    def test_calendar_drop_exports_and_backup(self):
        status, payload, headers = self.http("/api/calendar.ics")
        self.assertEqual(status, 200)
        self.assertIn("text/calendar", headers["content-type"])
        self.assertIn(b"BEGIN:VCALENDAR", payload)
        self.assertIn(b"Airdrop deadline", payload)

        status, payload, headers = self.http("/api/calendar.ics?drop=uniswap-uni")
        self.assertEqual(status, 200)
        self.assertIn(b"BEGIN:VCALENDAR", payload)

        status, payload, headers = self.http("/api/airdrops/demo-merkle/export/csv")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        self.assertIn(b"wallet,label", payload)
        self.assertIn(WALLET_UNCLAIMED.encode(), payload)

        status, payload, headers = self.http("/api/airdrops/demo-merkle/export/md")
        self.assertEqual(status, 200)
        self.assertIn(b"## Claim positions", payload)

        status, payload = self.json("/api/backup", "POST", {}, {"X-Idlescan": "1"})
        self.assertEqual(status, 200)
        backup = Path(payload["path"])
        self.assertTrue(backup.is_file())
        self.assertGreater(payload["size"], 0)

        status, payload = self.json("/api/backup", "POST", {})
        self.assertEqual(status, 400)


class WriteModeApiTests(ApiTestCase):
    read_only = False

    def test_config_reports_write_mode(self):
        status, payload = self.json("/api/config")
        self.assertEqual(status, 200)
        self.assertFalse(payload["read_only"])
        self.assertTrue(payload["write_enabled"])
        self.assertTrue(payload["warnings"])

    def test_prepare_and_confirm(self):
        status, payload = self.json(
            f"/api/claims/demo-merkle/{WALLET_UNCLAIMED}/prepare",
            "POST",
            {},
            {"X-Idlescan": "1"},
        )
        self.assertEqual(status, 200)
        tx = payload["transaction"]
        self.assertEqual(tx["chainId"], "0x1")
        self.assertTrue(tx["data"].startswith("0x"))
        self.assertEqual(len(payload["warnings"]), 3)

        status, payload = self.json(
            "/api/claims/confirm",
            "POST",
            {"drop_id": "demo-merkle", "wallet": WALLET_UNCLAIMED, "tx_hash": "0xbad"},
            {"X-Idlescan": "1"},
        )
        self.assertEqual(status, 400)

        status, payload = self.json(
            "/api/claims/confirm",
            "POST",
            {"drop_id": "demo-merkle", "wallet": WALLET_UNCLAIMED, "tx_hash": TX},
            {"X-Idlescan": "1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "submitted")

        status, payload = self.json("/api/audit")
        actions = [row["action"] for row in payload["audit"]]
        self.assertIn("claim.prepare", actions)
        self.assertIn("claim.confirm", actions)

    def test_prepare_claimed_position_is_conflict(self):
        status, payload = self.json(
            f"/api/claims/demo-merkle/{WALLET_CLAIMED}/prepare",
            "POST",
            {},
            {"X-Idlescan": "1"},
        )
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
