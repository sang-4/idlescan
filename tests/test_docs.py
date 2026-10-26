"""Doc-sync tests: API routes and environment variables must be documented."""

import re
import unittest
from pathlib import Path

from idlescan.api import build_router
from idlescan.config import load_settings

API_DOC = Path("docs/API.md")
CONFIG_DOC = Path("docs/CONFIGURATION.md")


class ApiDocSyncTests(unittest.TestCase):
    """Every registered API route must appear in docs/API.md."""

    def setUp(self):
        self.router = build_router()
        self.doc = API_DOC.read_text()

    def test_routes_are_documented(self):
        missing = []
        for method, pattern, _handler in self.router.routes:
            path = pattern.pattern.strip("^$")
            # Convert regex named groups to doc-style placeholders
            doc_path = re.sub(r"\(\?P<\w+>.+?\)", "{}", path)
            doc_path = doc_path.replace("\\", "")
            # Check the static prefix appears in the doc
            static_prefix = doc_path.split("{")[0] if "{" in doc_path else doc_path
            static_prefix = static_prefix.rstrip("/")
            if static_prefix and static_prefix not in self.doc:
                missing.append(f"{method} {path}")
        if missing:
            self.fail(f"API routes not documented in docs/API.md: {missing}")


class ConfigDocSyncTests(unittest.TestCase):
    """Every environment variable parsed in config.py must be documented."""

    def setUp(self):
        self.doc = CONFIG_DOC.read_text()

    def test_env_vars_are_documented(self):
        # Collect IDLESCAN_* keys from Settings.load_settings() source
        import inspect

        source = inspect.getsource(load_settings)
        keys = set(re.findall(r'"(IDLESCAN_[A-Z_0-9]+)"', source))
        missing = [key for key in sorted(keys) if key not in self.doc]
        if missing:
            self.fail(
                f"Environment variables not documented in docs/CONFIGURATION.md: {missing}"
            )


if __name__ == "__main__":
    unittest.main()
