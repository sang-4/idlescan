"""``idlescan about``: required fields, no secrets, no network."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from idlescan.about import collect, render_text
from idlescan.cli import main
from idlescan.config import Settings


class AboutPayloadTests(unittest.TestCase):
    def test_required_fields_present(self):
        settings = Settings()
        payload = collect(settings)
        for key in (
            "version",
            "python",
            "os",
            "schema_version",
            "chains_configured",
            "claim_handoff",
            "remote_guard",
            "database",
            "dataset_dir_present",
            "license",
        ):
            self.assertIn(key, payload)

    def test_absolute_database_path_is_reduced_to_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "private" / "idlescan.db"
            payload = collect(Settings(db_path=db))
        self.assertEqual(payload["database"], "idlescan.db")
        self.assertNotIn(tmp, json.dumps(payload))

    def test_relative_database_path_is_kept_relative(self):
        payload = collect(Settings(db_path=Path("data/idlescan.db")))
        self.assertEqual(payload["database"], "data/idlescan.db")

    def test_missing_database_is_reported_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "absent.db"
            payload = collect(Settings(db_path=db))
            self.assertEqual(payload["schema_version"], "not created")
            self.assertFalse(db.exists())

    def test_dataset_directory_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = collect(Settings(dataset_dir=Path(tmp)))
        self.assertTrue(payload["dataset_dir_present"])

    def test_remote_guard_states(self):
        self.assertEqual(collect(Settings()).get("remote_guard"), "loopback-only")
        self.assertIn(
            "INSECURE REMOTE",
            collect(Settings(host="0.0.0.0", allow_insecure_remote=True))["remote_guard"],
        )
        self.assertIn(
            "auth token",
            collect(Settings(host="0.0.0.0", auth_token="x"))["remote_guard"],
        )

    def test_text_rendering_has_every_declared_field(self):
        text = render_text(collect(Settings()))
        for label in ("product", "version", "license", "python", "os", "schema",
                      "chains", "claim hand-off", "remote guard", "database"):
            self.assertIn(label, text)


class AboutSecrecyTests(unittest.TestCase):
    def test_environment_secrets_never_appear(self):
        secret = "S3CRET-TOKEN-value-0123456789"
        rpc = "https://mainnet.example.com/v3/APIKEY-abcdef"
        wallet = "0x1111111111111111111111111111111111111111"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "idlescan.db"
            env = {
                "IDLESCAN_AUTH_TOKEN": secret,
                "IDLESCAN_RPC_ETHEREUM": rpc,
                "IDLESCAN_NOTIFY_WEBHOOK_URLS": f"https://hooks.example.com/{secret}",
                "IDLESCAN_WALLETS": wallet,
            }
            with patch.dict("os.environ", env, clear=False):
                from idlescan.config import load_settings

                payload = collect(load_settings(None, environ=env).with_overrides(db_path=db))
        blob = json.dumps(payload) + render_text(payload)
        for value in (secret, rpc, wallet, "APIKEY", "hooks.example.com"):
            self.assertNotIn(value, blob)


class AboutCliTests(unittest.TestCase):
    def test_json_output_and_exit_code(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["about", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["product"], "idlescan Desk")
        self.assertIn("version", payload)

    def test_text_output(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["about"])
        self.assertEqual(code, 0)
        self.assertIn("idlescan Desk", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
