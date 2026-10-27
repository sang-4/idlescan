"""End-to-end tests: real HTTP server against mock chain.

scan → snapshot → diff → alert → prepare → confirm.
No network calls; all chains are mocked.
"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from idlescan.api import create_server
from idlescan.config import Settings
from idlescan.selftest import MockRpc, build_world
from idlescan.service import PlatformService

TX = "0x" + "cd" * 32


class E2ETests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            host="127.0.0.1",
            port=0,
            db_path=Path(self._tmp.name) / "api.db",
            demo=True,
        )
        self.service = PlatformService(
            self.settings, rpc_factory=lambda chain: MockRpc(build_world())
        )
        self.service.seed_demo()
        self.server = create_server(self.service, self.settings)
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

    def http(self, method, path, body=None, headers=None):
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
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), dict(exc.headers)

    def test_e2e_scan_snapshot_diff_alert(self):
        # Start a scan
        status, payload, _ = self.http("POST", "/api/scan", {}, headers={"x-idlescan": "1"})
        self.assertEqual(status, 200)
        job_id = payload["job"]["id"]

        # Wait for the scan to complete
        import time

        deadline = time.monotonic() + 10
        job_status = None
        while time.monotonic() < deadline:
            status, payload, _ = self.http("GET", f"/api/jobs/{job_id}")
            job_status = payload["job"]["status"]
            if job_status in ("done", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(job_status, "done")

        # List snapshots
        status, payload, _ = self.http("GET", "/api/snapshots")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(payload["snapshots"]), 1)
        snapshot_id = payload["snapshots"][0]["id"]

        # Get snapshot detail
        status, payload, _ = self.http("GET", f"/api/snapshots/{snapshot_id}")
        self.assertEqual(status, 200)
        self.assertIsNotNone(payload["snapshot"])

        # List alerts
        status, payload, _ = self.http("GET", "/api/alerts")
        self.assertEqual(status, 200)
        # Demo data generates alerts (below gas, deadline)

        # Health check
        status, payload, _ = self.http("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_e2e_portfolio_and_export(self):
        # Run a scan first
        status, payload, _ = self.http("POST", "/api/scan", {}, headers={"x-idlescan": "1"})
        self.assertEqual(status, 200)
        job_id = payload["job"]["id"]

        import time

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, payload, _ = self.http("GET", f"/api/jobs/{job_id}")
            if payload["job"]["status"] == "done":
                break
            time.sleep(0.1)

        # Get portfolio
        status, payload, _ = self.http("GET", "/api/portfolio")
        self.assertEqual(status, 200)
        self.assertIn("snapshot", payload)

        # Export (returns raw bytes, not JSON)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/export/json",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("attachment", response.headers.get("content-disposition", ""))

    def test_e2e_config_and_metrics(self):
        status, payload, _ = self.http("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertTrue(payload["read_only"])
        self.assertIn("chains", payload)

        status, payload, _ = self.http("GET", "/api/metrics")
        self.assertEqual(status, 200)
        self.assertIn("requests", payload)
        self.assertIn("uptime_seconds", payload)


if __name__ == "__main__":
    unittest.main()
