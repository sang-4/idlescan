"""SSRF guard: private addresses, rebinding, redirects, size and content type."""

import unittest
from urllib.parse import urlsplit

from idlescan.safe_http import (
    BlockedURL,
    FetchError,
    FetchTooLarge,
    UnsupportedContentType,
    safe_fetch,
    safe_fetch_json,
    validate_url,
)


def resolver_for(*addresses):
    return lambda host, port: list(addresses)


class ValidationTests(unittest.TestCase):
    def test_https_public_host_pins_first_address(self):
        ip = validate_url(
            "https://datasets.example.org/file.json",
            resolver=resolver_for("93.184.216.34", "93.184.216.35"),
        )
        self.assertEqual(ip, "93.184.216.34")

    def test_http_requires_escape_hatch(self):
        with self.assertRaises(BlockedURL):
            validate_url("http://127.0.0.1:8000/x", resolver=resolver_for("93.184.216.34"))
        self.assertEqual(
            validate_url(
                "http://127.0.0.1:8000/x",
                allow_http=True,
                resolver=resolver_for("93.184.216.34"),
            ),
            "93.184.216.34",
        )

    def test_private_loopback_and_metadata_literals_are_blocked(self):
        for url in (
            "https://127.0.0.1/x",
            "https://10.0.0.1/x",
            "https://192.168.1.1/x",
            "https://169.254.169.254/latest/meta-data/",
            "https://[::1]/x",
            "https://[fe80::1]/x",
            "https://[fc00::1]/x",
            "https://[::ffff:127.0.0.1]/x",
        ):
            with self.subTest(url=url), self.assertRaises(BlockedURL):
                validate_url(url, require_resolution=False)

    def test_dns_rebinding_to_private_is_blocked(self):
        with self.assertRaises(BlockedURL) as ctx:
            validate_url(
                "https://rebind.example.org/x",
                resolver=resolver_for("93.184.216.34", "10.0.0.7"),
            )
        self.assertIn("non-public", str(ctx.exception))

    def test_ipv6_global_address_is_allowed(self):
        self.assertEqual(
            validate_url(
                "https://[2606:4700:4700::1111]/x",
                resolver=resolver_for("2606:4700:4700::1111"),
            ),
            "2606:4700:4700::1111",
        )

    def test_credentials_are_refused(self):
        with self.assertRaises(BlockedURL):
            validate_url("https://user:pass@example.org/x", require_resolution=False)

    def test_allowlist_is_exclusive(self):
        with self.assertRaises(BlockedURL):
            validate_url(
                "https://other.example.org/x",
                allowed_hosts=["datasets.example.org"],
                resolver=resolver_for("93.184.216.34"),
            )
        self.assertEqual(
            validate_url(
                "https://datasets.example.org/x",
                allowed_hosts=["datasets.example.org"],
                resolver=resolver_for("93.184.216.34"),
            ),
            "93.184.216.34",
        )

    def test_subdomain_allowlist_syntax(self):
        self.assertEqual(
            validate_url(
                "https://cdn.example.org/x",
                allowed_hosts=[".example.org"],
                resolver=resolver_for("93.184.216.34"),
            ),
            "93.184.216.34",
        )


def response(status=200, content_type="application/json", body=b"{}", location=None):
    headers = {"content-type": content_type}
    if location:
        headers["location"] = location
    return status, headers, body


class FetchTests(unittest.TestCase):
    def test_fetches_validated_ip_and_keeps_host_in_url(self):
        seen = {}

        def transport(url, ip, **kwargs):
            seen["url"] = url
            seen["ip"] = ip
            return response(body=b'{"ok": true}')

        result = safe_fetch(
            "https://datasets.example.org/a.json",
            resolver=resolver_for("93.184.216.34"),
            transport=transport,
        )
        self.assertEqual(result.json(), {"ok": True})
        self.assertEqual(seen["ip"], "93.184.216.34")
        self.assertEqual(urlsplit(seen["url"]).hostname, "datasets.example.org")

    def test_redirect_to_private_address_is_blocked(self):
        def transport(url, ip, **kwargs):
            return response(302, location="https://127.0.0.1/secret")

        with self.assertRaises(BlockedURL):
            safe_fetch(
                "https://datasets.example.org/a.json",
                resolver=lambda host, port: (
                    ["127.0.0.1"]
                    if host == "127.0.0.1"
                    else ["93.184.216.34"]
                ),
                transport=transport,
            )

    def test_redirect_scheme_change_is_refused(self):
        def transport(url, ip, **kwargs):
            return response(302, location="http://datasets.example.org/a.json")

        with self.assertRaises(BlockedURL):
            safe_fetch(
                "https://datasets.example.org/a.json",
                resolver=resolver_for("93.184.216.34"),
                transport=transport,
            )

    def test_redirect_limit(self):
        def transport(url, ip, **kwargs):
            return response(302, location="https://datasets.example.org/again")

        with self.assertRaises(FetchError) as ctx:
            safe_fetch(
                "https://datasets.example.org/a.json",
                resolver=resolver_for("93.184.216.34"),
                transport=transport,
            )
        self.assertIn("too many redirects", str(ctx.exception))

    def test_oversized_body_is_rejected(self):
        def transport(url, ip, **kwargs):
            return response(body=b"x" * 100)

        with self.assertRaises(FetchTooLarge):
            safe_fetch(
                "https://datasets.example.org/a.json",
                resolver=resolver_for("93.184.216.34"),
                transport=transport,
                max_bytes=50,
            )

    def test_wrong_content_type_is_rejected(self):
        def transport(url, ip, **kwargs):
            return response(content_type="text/html")

        with self.assertRaises(UnsupportedContentType):
            safe_fetch(
                "https://datasets.example.org/a.json",
                resolver=resolver_for("93.184.216.34"),
                transport=transport,
            )

    def test_json_helper_parses_object(self):
        def transport(url, ip, **kwargs):
            return response(body=b'{"claims": []}')

        payload = safe_fetch_json(
            "https://datasets.example.org/a.json",
            resolver=resolver_for("93.184.216.34"),
            transport=transport,
        )
        self.assertEqual(payload, {"claims": []})


if __name__ == "__main__":
    unittest.main()
