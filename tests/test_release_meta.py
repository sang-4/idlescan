"""Release metadata invariants: version, licence and seller identity."""

import re
import unittest
from pathlib import Path

from idlescan import __version__

ROOT = Path(__file__).resolve().parent.parent
SHIPPED_DOCS = [
    "README.md",
    "QUICKSTART.md",
    "TRUST.md",
    "SUPPORT.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY.md",
    "docs/ROADMAP.md",
]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "version not found in pyproject.toml"
    return match.group(1)


class VersionTests(unittest.TestCase):
    def test_package_and_pyproject_versions_match(self):
        self.assertEqual(__version__, _pyproject_version())

    def test_version_is_semver(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")


class LicenceMetadataTests(unittest.TestCase):
    def setUp(self):
        self.pyproject = (ROOT / "pyproject.toml").read_text()

    def test_license_metadata_is_proprietary(self):
        self.assertIn('license = { text = "Proprietary" }', self.pyproject)
        self.assertIn("License :: Other/Proprietary License", self.pyproject)
        self.assertNotIn("License :: OSI Approved", self.pyproject)
        self.assertNotIn('license = { text = "MIT" }', self.pyproject)

    def test_author_and_urls(self):
        self.assertIn("Allan Kiprop", self.pyproject)
        self.assertIn("https://allank.dev", self.pyproject)
        self.assertNotIn("Xtanol", self.pyproject)

    def test_license_and_notice_files(self):
        licence = (ROOT / "LICENSE").read_text()
        notice = (ROOT / "NOTICE").read_text()
        self.assertIn("Allan Kiprop", licence)
        self.assertIn("Copyright (c) 2026 Allan Kiprop", licence)
        self.assertIn("All rights reserved", licence)
        self.assertIn("single-user", licence.lower())
        self.assertIn("See LICENSE", notice)

    def test_no_mit_license_claims_in_shipped_docs(self):
        stale = "mit " + "license"  # split so this assertion is not itself a hit
        for name in SHIPPED_DOCS:
            path = ROOT / name
            if not path.exists():
                continue
            text = path.read_text().lower()
            self.assertNotIn(stale, text, name)
            self.assertNotIn("xtanol", text, name)

    def test_no_banned_seller_name(self):
        for path in ["LICENSE", "NOTICE", "README.md", "pyproject.toml"]:
            self.assertNotIn("xtanol", (ROOT / path).read_text().lower(), path)


if __name__ == "__main__":
    unittest.main()
