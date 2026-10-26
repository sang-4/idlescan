"""Settings / .env contract, including the read-only default and remote-write refusal."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import idlescan.config as config_module
from idlescan.config import (
    PROJECT_ROOT,
    Settings,
    SettingsError,
    load_settings,
    parse_env_file,
)


class ParseEnvFileTests(unittest.TestCase):
    def test_comments_quotes_and_export_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\n"
                "\n"
                "IDLESCAN_READ_ONLY='false'\n"
                'export IDLESCAN_HOST="0.0.0.0"\n'
                "IDLESCAN_CHAINS=ethereum, base ,arbitrum\n"
            )
            values = parse_env_file(path)
        self.assertEqual(values["IDLESCAN_READ_ONLY"], "false")
        self.assertEqual(values["IDLESCAN_HOST"], "0.0.0.0")
        self.assertEqual(values["IDLESCAN_CHAINS"], "ethereum, base ,arbitrum")
        self.assertNotIn("comment", values)

    def test_malformed_line_is_rejected_with_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("IDLESCAN_READ_ONLY=true\nthis is not an assignment\n")
            with self.assertRaises(SettingsError) as ctx:
                parse_env_file(path)
        self.assertIn(":2:", str(ctx.exception))

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(parse_env_file(Path(tmp) / "nope"), {})


class SettingsTests(unittest.TestCase):
    def setUp(self):
        # Tests must not inherit the developer's real .env; only explicit paths count.
        real_find = config_module.find_env_file

        def only_explicit(explicit=None):
            return real_find(explicit) if explicit else None

        patcher = patch("idlescan.config.find_env_file", side_effect=only_explicit)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_only_is_the_default(self):
        settings = load_settings(environ={})
        self.assertTrue(settings.read_only)
        self.assertFalse(settings.write_enabled)
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8787)
        self.assertEqual(settings.validate(), [])
        self.assertTrue(settings.db_path.is_absolute())

    def test_env_file_switches_write_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "IDLESCAN_READ_ONLY=false\n"
                "IDLESCAN_HOST=127.0.0.1\n"
                "IDLESCAN_PORT=9001\n"
                "IDLESCAN_CHAINS=ethereum,base\n"
                "IDLESCAN_MIN_USD=5\n"
                "IDLESCAN_REFRESH_MINUTES=15\n"
            )
            settings = load_settings(path, environ={})
        self.assertFalse(settings.read_only)
        self.assertTrue(settings.write_enabled)
        self.assertEqual(settings.port, 9001)
        self.assertEqual(settings.chains, ["ethereum", "base"])
        self.assertEqual(settings.min_usd, 5.0)
        self.assertEqual(settings.refresh_minutes, 15)
        warnings = settings.validate()
        self.assertEqual(len(warnings), 1)
        self.assertIn("WRITE MODE", warnings[0])

    def test_environment_beats_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("IDLESCAN_READ_ONLY=true\nIDLESCAN_PORT=1111\n")
            settings = load_settings(path, environ={"IDLESCAN_READ_ONLY": "false", "IDLESCAN_PORT": "2222"})
        self.assertFalse(settings.read_only)
        self.assertEqual(settings.port, 2222)

    def test_remote_write_mode_is_refused_without_opt_in(self):
        settings = Settings(read_only=False, host="0.0.0.0")
        with self.assertRaises(SettingsError) as ctx:
            settings.validate()
        self.assertIn("non-loopback", str(ctx.exception))

    def test_remote_write_mode_allowed_with_explicit_opt_in(self):
        settings = Settings(read_only=False, host="0.0.0.0", allow_remote_write=True)
        self.assertEqual(len(settings.validate()), 1)

    def test_remote_read_only_is_fine(self):
        settings = Settings(read_only=True, host="0.0.0.0")
        self.assertEqual(settings.validate(), [])

    def test_invalid_values_are_reported(self):
        with self.assertRaises(SettingsError):
            load_settings(environ={"IDLESCAN_READ_ONLY": "maybe"})
        with self.assertRaises(SettingsError):
            load_settings(environ={"IDLESCAN_PORT": "http"})
        with self.assertRaises(SettingsError):
            load_settings(environ={"IDLESCAN_MIN_USD": "lots"})

    def test_missing_explicit_env_file_is_an_error(self):
        with self.assertRaises(SettingsError):
            load_settings("/definitely/not/here/.env", environ={})

    def test_relative_db_path_resolves_against_project_root(self):
        settings = load_settings(environ={"IDLESCAN_DB": "data/test.db"})
        self.assertEqual(settings.db_path, (PROJECT_ROOT / "data" / "test.db").resolve())

    def test_overrides_ignore_none(self):
        settings = load_settings(environ={})
        updated = settings.with_overrides(port=9999, host=None, db_path=None)
        self.assertEqual(updated.port, 9999)
        self.assertEqual(updated.host, settings.host)
        self.assertEqual(updated.db_path, settings.db_path)


if __name__ == "__main__":
    unittest.main()
