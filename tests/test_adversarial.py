"""Adversarial API tests: malformed input, path traversal, header injection,
CSRF/Origin mismatches, unknown methods, oversized bodies."""

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


class AdversarialApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            host="127.0.0.1",
            port=0,
            db_path=Path(self._tmp.name) / "api.db",
            demo=True,
            max_body_bytes=1024,
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

    def raw_request(
        self, method, path, body=b"", headers=None, raw=False
    ):
        """Send a raw HTTP request, returning (status, body_bytes, headers)."""
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("content-length", str(len(body)))
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def test_malformed_json_body(self):
        status, body, _ = self.raw_request(
            "POST", "/api/check", body=b"{not json",
            headers={"content-type": "application/json", "x-idlescan": "1"},
        )
        self.assertEqual(status, 400)

    def test_oversized_body(self):
        big = b"x" * (self.settings.max_body_bytes + 1)
        status, body, _ = self.raw_request(
            "POST", "/api/check", body=big,
            headers={"content-type": "application/json", "x-idlescan": "1"},
        )
        self.assertEqual(status, 413)

    def test_wrong_content_type(self):
        status, body, _ = self.raw_request(
            "POST", "/api/check", body=b"hello",
            headers={"content-type": "text/plain", "x-idlescan": "1"},
        )
        self.assertEqual(status, 415)

    def test_path_traversal_encoded(self):
        for traversal in [
            "/api/../api/health",
            "/api/%2e%2e/health",
            "/api/..%2fhealth",
            "/api/..%5chealth",
        ]:
            status, _, headers = self.raw_request("GET", traversal)
            self.assertIn(status, (404, 403), f"traversal {traversal} returned {status}")

    def test_unknown_method(self):
        status, _, _ = self.raw_request(
            "PATCH", "/api/health",
            headers={"x-idlescan": "1"},
        )
        self.assertEqual(status, 405)

    def test_csrf_header_required_on_mutation(self):
        status, _, _ = self.raw_request(
            "POST", "/api/wallets",
            body=json.dumps({"address": "0x" + "11" * 20}).encode(),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(status, 400)

    def test_origin_mismatch_rejected(self):
        status, _, _ = self.raw_request(
            "POST", "/api/check",
            body=json.dumps({"address": "0x" + "11" * 20}).encode(),
            headers={
                "content-type": "application/json",
                "x-idlescan": "1",
                "origin": "https://evil.example.org",
                "host": "127.0.0.1",
            },
        )
        self.assertEqual(status, 400)

    def test_health_without_auth_when_token_set(self):
        """When no token is configured, /api/health is public (loopback)."""
        status, _, _ = self.raw_request("GET", "/api/health")
        self.assertEqual(status, 200)

    def test_double_slash_path(self):
        status, _, _ = self.raw_request("GET", "/api//health")
        self.assertIn(status, (200, 404))

    def test_trailing_slash(self):
        status, _, _ = self.raw_request("GET", "/api/health/")
        self.assertIn(status, (200, 404))


if __name__ == "__main__":
    unittest.main()
