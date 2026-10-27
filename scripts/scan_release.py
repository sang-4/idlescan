#!/usr/bin/env python3
"""Fail the release if secrets, personal paths or banned words leak into it.

Scans the built zip, the wheel and the sdist. Standard library only.

Usage:
    python scripts/scan_release.py [--archive PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE = ROOT / "release" / "dist" / "idlescan-desk-1.2.0.zip"
SUPPORT_EMAIL = "support@allank.dev"

PRIVATE_KEY = re.compile(r"(?<!0x)\b[0-9a-fA-F]{64}\b")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HOME_PATH = re.compile(r"(?i)(/home/|/Users/|[A-Z]:\\Users\\|[A-Z]:/Users/)")
KEYED_URL = re.compile(
    r"(?i)https?://[^\s\"'<>]*(?:api[_-]?key|apikey|access[_-]?token|secret)=[^\s\"'<>]+"
)
# Only quoted literals: `token = self.settings.auth_token` is code, not a value.
QUOTED_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|apikey|secret|password|passwd|access[_-]?token|auth[_-]?token)\b"
    r"\s*[:=]\s*[\"']([^\"']{8,})[\"']"
)
# dotenv-style credential lines, e.g. IDLESCAN_AUTH_TOKEN=abc123
ENV_SECRET = re.compile(
    r"(?m)^\s*(?:export\s+)?([A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD))\s*=\s*(\S+)"
)
PLACEHOLDER_VALUES = {
    "change-me",
    "changeme",
    "placeholder",
    "example",
    "your-token",
    "redacted",
    "none",
    "null",
    "true",
    "false",
}
BANNED_WORDS = ("xtanol",)
ALLOWED_EMAILS = {SUPPORT_EMAIL}
BINARY_SUFFIXES = {".whl", ".gz", ".png", ".ico", ".jpg", ".jpeg", ".woff2", ".zip"}


def _text(data: bytes) -> str:
    return data.decode("utf-8", "ignore")


def _scan_text(name: str, text: str, findings: list[str]) -> None:
    lowered_name = name.lower()
    for _match in PRIVATE_KEY.finditer(text):
        if lowered_name.endswith("checksums.txt"):
            continue
        findings.append(f"{name}: private-key-like 64-hex string")
        break
    for match in EMAIL.finditer(text):
        address = match.group(0).lower()
        if address not in ALLOWED_EMAILS and not address.endswith("@idlescan.local"):
            findings.append(f"{name}: unexpected email address {match.group(0)!r}")
    for match in HOME_PATH.finditer(text):
        findings.append(f"{name}: absolute home path {match.group(0)!r}")
    for _match in KEYED_URL.finditer(text):
        findings.append(f"{name}: URL with an embedded credential")
        break
    for pattern in (QUOTED_ASSIGNMENT, ENV_SECRET):
        for match in pattern.finditer(text):
            value = match.group(2).strip().strip('"\'').lower()
            if value in PLACEHOLDER_VALUES or value.startswith("<") or "{{" in value:
                continue
            if value in {"data/idlescan.db", "true", "false", "null", "none", "0"}:
                continue
            # Public contract addresses (mock or real) are not credentials.
            if value.startswith("0x") and len(value) <= 42:
                continue
            findings.append(f"{name}: possible secret assignment ({match.group(1)})")
            break
    for word in BANNED_WORDS:
        if word in text.lower():
            findings.append(f"{name}: banned word {word!r}")


def _is_binary(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in BINARY_SUFFIXES)


def scan_zip(path: Path, findings: list[str]) -> None:
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or _is_binary(info.filename):
                continue
            data = archive.read(info)
            _scan_text(f"{path.name}:{info.filename}", _text(data), findings)


def scan_tar(path: Path, findings: list[str]) -> None:
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            _scan_text(f"{path.name}:{member.name}", _text(handle.read()), findings)


def scan_tree(path: Path, findings: list[str]) -> None:
    for item in sorted(path.rglob("*")):
        if item.is_file() and not _is_binary(item.name):
            _scan_text(item.name, _text(item.read_bytes()), findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    args = parser.parse_args(argv)
    archive: Path = args.archive
    if not archive.exists():
        print(f"missing archive: {archive}", file=sys.stderr)
        return 2

    findings: list[str] = []
    scan_zip(archive, findings)

    extract_root = archive.parent / "_scan"
    if extract_root.exists():
        import shutil

        shutil.rmtree(extract_root)
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_root)
        for item in sorted(extract_root.rglob("*")):
            if not item.is_file():
                continue
            if item.suffix == ".whl":
                scan_zip(item, findings)
            elif item.suffix == ".gz" and item.name.endswith(".tar.gz"):
                scan_tar(item, findings)
        scan_tree(extract_root, findings)
    finally:
        import shutil

        shutil.rmtree(extract_root, ignore_errors=True)

    if findings:
        print(f"FAIL: {len(findings)} finding(s)")
        for finding in findings:
            print("  -", finding)
        return 1
    print(f"PASS: no secrets, personal paths or banned words in {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
