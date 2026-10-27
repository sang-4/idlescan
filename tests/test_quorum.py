"""Quorum reads, endpoint health, overrides and secret redaction."""

import time
import unittest

from idlescan.config import Settings
from idlescan.redaction import redact_url
from idlescan.rpc import EndpointHealth, QuorumError, Rpc, RpcError
from idlescan.selftest import build_config

A = "https://rpc-a.example.org/?apikey=SECRET-A"
B = "https://rpc-b.example.org/?apikey=SECRET-B"
C = "https://rpc-c.example.org"


def fake_post(values, *, error_urls=()):
    def post(url, payload):
        if url in error_urls:
            raise RpcError(f"{url}: provider exploded")
        method = payload["method"]
        if method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": payload["id"], "result": values[url]}
        return {"jsonrpc": "2.0", "id": payload["id"], "result": values[url]}

    return post


class QuorumTests(unittest.TestCase):
    def test_agreeing_endpoints_return_value(self):
        rpc = Rpc([A, B], quorum=2, post=fake_post({A: "0x01", B: "0x01"}))
        self.assertEqual(rpc.request_quorum("eth_chainId"), "0x01")
        states = {item["url"]: item for item in rpc.health.snapshot()}
        self.assertEqual(sum(state["successes"] for state in states.values()), 2)

    def test_disagreement_lists_redacted_endpoints(self):
        rpc = Rpc([A, B], quorum=2, post=fake_post({A: "0x01", B: "0x02"}))
        with self.assertRaises(QuorumError) as ctx:
            rpc.request_quorum("eth_chainId")
        message = str(ctx.exception)
        self.assertIn("quorum 2 not reached", message)
        self.assertIn("rpc-a.example.org", message)
        self.assertIn("rpc-b.example.org", message)
        self.assertNotIn("SECRET", message)

    def test_single_endpoint_falls_back_to_normal_request(self):
        rpc = Rpc([A], quorum=2, post=fake_post({A: "0x01"}))
        self.assertEqual(rpc.request_quorum("eth_chainId"), "0x01")

    def test_stale_endpoint_is_deprioritised(self):
        now = int(time.time())

        def post(url, payload):
            if payload["method"] == "eth_blockNumber":
                return {"jsonrpc": "2.0", "id": payload["id"], "result": hex(100)}
            if payload["method"] == "eth_getBlockByNumber":
                stamp = now - 10 if url != C else now - 10_000
                return {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"number": hex(100), "timestamp": hex(stamp)},
                }
            value = "0x01" if url != C else "0x99"
            return {"jsonrpc": "2.0", "id": payload["id"], "result": value}

        rpc = Rpc([C, A, B], quorum=2, post=post)
        stale = rpc.refresh_endpoint_health(stale_seconds=3600)
        self.assertEqual([item["url"] for item in stale], [redact_url(C)])
        self.assertEqual(rpc.request_quorum("eth_chainId"), "0x01")

    def test_partial_endpoint_failure_fails_closed(self):
        # One endpoint cannot satisfy a quorum of two.  A provider outage must
        # leave the answer unknown rather than silently downgrade to one node.
        rpc = Rpc([A, B], quorum=2, post=fake_post({A: "0x01", B: "0x01"}, error_urls={A}))
        with self.assertRaises(QuorumError):
            rpc.request_quorum("eth_chainId")
        states = {item["url"]: item for item in rpc.health.snapshot()}
        self.assertEqual(states[redact_url(A)]["errors"], 1)
        self.assertEqual(states[redact_url(B)]["successes"], 1)

    def test_health_snapshot_redacts_urls_and_errors(self):
        rpc = Rpc([A], retries=1, post=fake_post({}, error_urls={A}))
        with self.assertRaises(RpcError) as ctx:
            rpc.request("eth_chainId")
        self.assertNotIn("SECRET", str(ctx.exception))
        snapshot = rpc.health.snapshot()
        self.assertNotIn("SECRET", str(snapshot))
        self.assertEqual(snapshot[0]["url"], "https://rpc-a.example.org/")

    def test_health_tracks_error_rate(self):
        health = EndpointHealth()
        health.success(A, 12.0)
        health.failure(A, "boom")
        state = health.snapshot()[0]
        self.assertEqual(state["successes"], 1)
        self.assertEqual(state["errors"], 1)
        self.assertEqual(state["error_rate"], 0.5)
        self.assertEqual(state["latency_ms"], 12.0)


class OverrideTests(unittest.TestCase):
    def test_rpc_override_takes_precedence(self):
        chains, _, _ = build_config()
        chain = chains.get("ethereum")
        settings = Settings(rpc_overrides={"ethereum": ["https://override.example.org"]})
        self.assertEqual(settings.rpc_endpoints(chain), ["https://override.example.org"])
        self.assertEqual(Settings().rpc_endpoints(chain), list(chain.rpcs))

    def test_prepare_rpc_uses_quorum_two_when_available(self):
        from idlescan.service import PlatformService

        settings = Settings(rpc_overrides={"ethereum": ["https://a.example.org", "https://b.example.org"]})
        chains, _, _ = build_config()
        chain = chains.get("ethereum")
        # Inspect the factory decision without starting a server or touching network.
        service = PlatformService.__new__(PlatformService)
        service.settings = settings
        service.rpc_factory = None
        service.endpoint_health = EndpointHealth()
        rpc = PlatformService._chain_rpc(service, chain)
        self.assertEqual(rpc.quorum, 2)
        self.assertEqual(rpc.urls, ["https://a.example.org", "https://b.example.org"])


if __name__ == "__main__":
    unittest.main()
