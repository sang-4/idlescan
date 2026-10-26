"""Observability: request metrics, health detail and structured logs."""

import io
import json
import logging
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from idlescan.api import ServerMetrics, create_server, metric_path
from idlescan.config import Settings
from idlescan.log import JsonFormatter, RedactingFilter, configure_logging, get_logger
from idlescan.selftest import MockRpc, build_world
from idlescan.service import PlatformService

SECRET = "s3cret-token-value"
RPC_SECRET = "https://user:pass@rpc.example.org/v1/KEY?token=RPC-SECRET"


class MetricPathTests(unittest.TestCase):
    def test_wallet_addresses_are_placeholders(self):
        address = "0x" + "ab" * 20
        self.assertEqual(
            metric_path(f"/api/wallets/{address}"), "/api/wallets/:address"
        )

    def test_numeric_ids_are_placeholders(self):
        self.assertEqual(metric_path("/api/alerts/42/ack"), "/api/alerts/:id/ack")
        self.assertEqual(metric_path("/api/snapshots/7"), "/api/snapshots/:id")

    def test_static_assets_are_one_bucket(self):
        self.assertEqual(metric_path("/index.html"), "static")
        self.assertEqual(metric_path("/app.css"), "static")

    def test_unknown_api_paths_stay_distinct(self):
        self.assertEqual(metric_path("/api/nope"), "/api/nope")


class ServerMetricsTests(unittest.TestCase):
    def test_counts_requests_and_status_families(self):
        metrics = ServerMetrics()
        for _ in range(4):
            metrics.begin()
        metrics.record("GET", "/api/health", 200, 1.5)
        metrics.record("GET", "/api/health", 500, 3.0)
        metrics.record("POST", "/api/scan", 429, 0.5)
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["requests"]["total"], 3)
        self.assertEqual(snapshot["requests"]["in_flight"], 1)
        self.assertEqual(snapshot["requests"]["errors"], 1)
        self.assertEqual(snapshot["requests"]["rate_limited"], 1)
        self.assertEqual(snapshot["requests"]["by_method"], {"GET": 2, "POST": 1})
        self.assertEqual(snapshot["requests"]["by_status"], {"2xx": 1, "4xx": 1, "5xx": 1})
        self.assertEqual(snapshot["paths"]["/api/health"], 2)

    def test_unauthorized_requests_are_counted(self):
        metrics = ServerMetrics()
        metrics.begin()
        metrics.record("GET", "/api/health", 401, 0.2)
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["requests"]["unauthorized"], 1)
        self.assertEqual(snapshot["requests"]["errors"], 0)

    def test_path_cardinality_is_capped(self):
        metrics = ServerMetrics(max_paths=2)
        for index in range(5):
            metrics.begin()
            metrics.record("GET", f"/api/airdrops/drop{index}", 200, 0.1)
        snapshot = metrics.snapshot()
        self.assertEqual(len(snapshot["paths"]), 2)
        self.assertEqual(snapshot["requests"]["overflowed_paths"], 3)
        self.assertEqual(snapshot["requests"]["total"], 5)

    def test_snapshot_is_json_serialisable_without_secrets(self):
        metrics = ServerMetrics()
        metrics.begin()
        metrics.record("GET", "/api/health", 200, 0.1)
        encoded = json.dumps(metrics.snapshot())
        self.assertNotIn(SECRET, encoded)


class ObservabilityApiTests(unittest.TestCase):
    token = SECRET

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            host="127.0.0.1",
            port=0,
            db_path=Path(self._tmp.name) / "api.db",
            demo=True,
            auth_token=self.token,
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

    def request(self, path, *, token=None):
        headers = {"authorization": f"Bearer {token or self.token}"}
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_metrics_requires_auth(self):
        status, payload = self.request("/api/metrics", token="wrong")
        self.assertEqual(status, 401)
        self.assertNotIn("requests", payload)

    def test_metrics_report_request_counts(self):
        self.request("/api/health")
        status, payload = self.request("/api/metrics")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["requests"]["total"], 1)
        self.assertIn("uptime_seconds", payload)
        self.assertEqual(payload["database"]["up_to_date"], True)
        self.assertEqual(payload["version"], "1.1.0")

    def test_metrics_bucket_wallet_addresses(self):
        address = "0x" + "11" * 20
        self.request(f"/api/wallets/{address}")
        _, payload = self.request("/api/metrics")
        self.assertIn("/api/wallets/:address", payload["paths"])
        self.assertNotIn(address, json.dumps(payload["paths"]))

    def test_metrics_count_404_and_401(self):
        self.request("/api/does-not-exist")
        self.request("/api/metrics", token="wrong")
        _, payload = self.request("/api/metrics")
        self.assertEqual(payload["requests"]["unauthorized"], 1)
        self.assertGreaterEqual(payload["requests"]["by_status"].get("4xx", 0), 2)

    def test_health_includes_database_and_request_stats(self):
        _, payload = self.request("/api/health")
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["database"]["up_to_date"])
        self.assertEqual(payload["database"]["schema_version"], payload["database"]["latest_schema_version"])
        self.assertIn("requests", payload)
        self.assertIn("uptime_seconds", payload)

    def test_health_reports_degraded_when_migration_is_pending(self):
        healthy = self.service.db.status()
        pending = dict(healthy, up_to_date=False, schema_version=1, pending_migrations=1)
        with mock.patch.object(self.service.db, "status", return_value=pending):
            _, payload = self.request("/api/health")
        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["database"]["up_to_date"])

    def test_responses_do_not_leak_the_auth_token_or_rpc_credentials(self):
        self.service.endpoint_health.failure(RPC_SECRET, "boom " + RPC_SECRET)
        _, health = self.request("/api/health")
        _, metrics = self.request("/api/metrics")
        for payload in (health, metrics):
            encoded = json.dumps(payload)
            self.assertNotIn(SECRET, encoded)
            self.assertNotIn("RPC-SECRET", encoded)
            self.assertNotIn("user:pass", encoded)
        self.assertIn("rpc", health)
        self.assertTrue(health["rpc"])


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()

    def tearDown(self):
        configure_logging("INFO", "text", stream=io.StringIO(), force=True)

    def test_text_logging_redacts_urls_and_tokens(self):
        configure_logging("INFO", "text", stream=self.stream, force=True)
        get_logger("idlescan.test").info("fetching %s", RPC_SECRET)
        output = self.stream.getvalue()
        self.assertIn("fetching", output)
        self.assertNotIn("RPC-SECRET", output)
        self.assertNotIn("user:pass", output)

    def test_json_logging_is_one_object_per_line(self):
        configure_logging("INFO", "json", stream=self.stream, force=True)
        logger = get_logger("idlescan.test")
        logger.info("ready", extra={"request_id": "abc", "status": 200})
        lines = [line for line in self.stream.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["message"], "ready")
        self.assertEqual(payload["request_id"], "abc")
        self.assertEqual(payload["status"], 200)

    def test_json_logging_redacts_secret_arguments(self):
        configure_logging("INFO", "json", stream=self.stream, force=True)
        get_logger("idlescan.test").warning("webhook %s", RPC_SECRET)
        output = self.stream.getvalue()
        self.assertNotIn("RPC-SECRET", output)
        self.assertEqual(json.loads(output)["message"], "webhook https://rpc.example.org/v1/KEY")

    def test_filter_scrubs_exception_text(self):
        record = logging.LogRecord(
            "idlescan.test", logging.ERROR, __file__, 1, "failed: %s", (RPC_SECRET,), None
        )
        RedactingFilter().filter(record)
        self.assertNotIn("RPC-SECRET", record.getMessage())

    def test_filter_handles_dict_arguments(self):
        record = logging.LogRecord(
            "idlescan.test",
            logging.INFO,
            __file__,
            1,
            "connect %(url)s",
            ({"url": RPC_SECRET},),
            None,
        )
        RedactingFilter().filter(record)
        self.assertNotIn("RPC-SECRET", record.getMessage())

    def test_json_formatter_has_stable_fields(self):
        record = logging.LogRecord(
            "idlescan.test", logging.INFO, __file__, 1, "hello", (), None
        )
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(
            list(payload), ["ts", "level", "logger", "message"]
        )

    def test_configure_is_idempotent_unless_forced(self):
        configure_logging("INFO", "text", stream=self.stream, force=True)
        first = logging.getLogger().handlers[0]
        configure_logging("DEBUG", "json", stream=io.StringIO())
        self.assertIs(logging.getLogger().handlers[0], first)
        configure_logging("DEBUG", "text", stream=self.stream, force=True)
        self.assertIsNot(logging.getLogger().handlers[0], first)


if __name__ == "__main__":
    unittest.main()
