"""Access control, request limits, lockdown and honest mode reporting."""

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


class ApiSecurityBase(unittest.TestCase):
    read_only = True
    token = None
    lockdown = False
    max_body = 1024
    scan_limit = 12

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            read_only=self.read_only,
            host="127.0.0.1",
            port=0,
            db_path=Path(self._tmp.name) / "api.db",
            demo=True,
            auth_token=self.token,
            lockdown=self.lockdown,
            max_body_bytes=self.max_body,
            rate_limit_loopback=True,
            rate_limit_scan_per_ten_minutes=self.scan_limit,
        )
        self.service = PlatformService(
            self.settings, rpc_factory=lambda chain: MockRpc(build_world())
        )
        self.service.seed_demo()
        self.server = create_server(
            self.service, self.settings, warnings=self.settings.validate()
        )
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
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
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
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            response_headers = {
                key.lower(): value for key, value in exc.headers.items()
            }
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            payload = raw
        return status, payload, response_headers


class AuthTests(ApiSecurityBase):
    token = "correct-horse-battery-staple"

    def auth(self):
        return {"authorization": f"Bearer {self.token}"}

    def test_api_requires_bearer_token(self):
        status, payload, headers = self.http("/api/config")
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "unauthorized")
        self.assertIn("bearer", headers["www-authenticate"].lower())

    def test_wrong_token_is_rejected(self):
        status, payload, _ = self.http(
            "/api/config", headers={"authorization": "Bearer wrong"}
        )
        self.assertEqual(status, 401)

    def test_correct_token_is_accepted(self):
        status, payload, _ = self.http("/api/config", headers=self.auth())
        self.assertEqual(status, 200)
        self.assertTrue(payload["auth_required"])

    def test_static_assets_stay_public(self):
        status, _, headers = self.http("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["content-type"])

    def test_csrf_header_still_required(self):
        status, payload, _ = self.http(
            "/api/wallets",
            method="POST",
            body={"address": "0x" + "11" * 20},
            headers=self.auth(),
        )
        self.assertEqual(status, 400)
        self.assertIn("X-Idlescan", payload["error"])

    def test_request_id_is_returned(self):
        _, _, headers = self.http("/api/config", headers=self.auth())
        self.assertRegex(headers["x-request-id"], r"^[0-9a-f]{16}$")


class RateLimitTests(ApiSecurityBase):
    scan_limit = 2

    def test_scan_bucket_returns_429_with_retry_after(self):
        for _ in range(2):
            status, _, _ = self.http("/api/check", method="POST", body={"address": "0x" + "11" * 20})
            self.assertNotEqual(status, 429)
        status, payload, headers = self.http(
            "/api/check", method="POST", body={"address": "0x" + "11" * 20}
        )
        self.assertEqual(status, 429)
        self.assertEqual(payload["code"], "rate_limited")
        self.assertIn("retry-after", headers)
        self.assertGreaterEqual(int(headers["retry-after"]), 1)

    def test_read_bucket_is_separate(self):
        for _ in range(2):
            self.http("/api/check", method="POST", body={"address": "0x" + "11" * 20})
        status, _, _ = self.http("/api/config")
        self.assertEqual(status, 200)


class BodyLimitTests(ApiSecurityBase):
    def test_oversized_body_is_413(self):
        status, payload, _ = self.http(
            "/api/check",
            method="POST",
            body=b'{"address":"' + b"a" * 4000 + b'"}',
            headers={"x-idlescan": "1"},
        )
        self.assertEqual(status, 413)
        self.assertEqual(payload["code"], "payload_too_large")

    def test_wrong_content_type_is_415(self):
        status, payload, _ = self.http(
            "/api/check",
            method="POST",
            body=b'{"address":"0x1111111111111111111111111111111111111111"}',
            headers={"content-type": "text/plain", "x-idlescan": "1"},
        )
        self.assertEqual(status, 415)
        self.assertEqual(payload["code"], "unsupported_media_type")

    def test_malformed_json_is_400(self):
        status, payload, _ = self.http(
            "/api/check", method="POST", body=b"{not json", headers={"x-idlescan": "1"}
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid JSON", payload["error"])

    def test_unknown_method_is_405(self):
        status, _, _ = self.http("/api/wallets", method="PUT", body={})
        self.assertEqual(status, 405)


class LockdownTests(ApiSecurityBase):
    lockdown = True

    def post(self, path, body=None):
        return self.http(
            path, method="POST", body={} if body is None else body,
            headers={"x-idlescan": "1"},
        )

    def test_config_and_health_report_lockdown(self):
        status, payload, _ = self.http("/api/config")
        self.assertEqual(status, 200)
        self.assertTrue(payload["lockdown"])
        self.assertFalse(payload["local_edits"])
        status, payload, _ = self.http("/api/health")
        self.assertTrue(payload["lockdown"])

    def test_mutations_are_locked(self):
        cases = [
            ("/api/wallets", {"address": "0x" + "11" * 20}),
            ("/api/wallets/bulk", {"wallets": ["0x" + "11" * 20]}),
            ("/api/airdrops", {"id": "x", "name": "X", "chain": "ethereum", "distributor": "0x" + "22" * 20}),
            ("/api/scan", {}),
            ("/api/alerts/1/ack", {}),
            ("/api/alerts/ack-all", {}),
            ("/api/backup", {}),
            ("/api/claims/confirm", {}),
        ]
        for path, body in cases:
            with self.subTest(path=path):
                status, payload, _ = self.post(path, body)
                self.assertEqual(status, 403)
                self.assertEqual(payload["code"], "lockdown")

    def test_check_and_reads_still_work(self):
        status, payload, _ = self.post(
            "/api/check", {"address": "0x1111111111111111111111111111111111111111"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["claims"])
        status, _, _ = self.http("/api/portfolio")
        self.assertEqual(status, 200)
        status, _, headers = self.http("/api/export/json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["content-type"])


class DefaultsTests(ApiSecurityBase):
    def test_loopback_defaults_are_unchanged(self):
        settings = Settings(host="127.0.0.1")
        self.assertEqual(settings.validate(), [])
        self.assertFalse(settings.rate_limit_enabled)
        self.assertFalse(settings.lockdown)
        self.assertEqual(settings.max_body_bytes, 1024 * 1024)
        self.assertIsNone(settings.auth_token)


if __name__ == "__main__":
    unittest.main()
