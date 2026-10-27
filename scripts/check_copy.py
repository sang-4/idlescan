#!/usr/bin/env python3
"""Reject marketing hype words in the listing copy and shipped documentation.

Scans release/listing/ and the buyer-facing documents. Exits non-zero on a hit.
LICENSE, NOTICE and THIRD_PARTY.md are exempt: legal and attribution text must be
able to use words such as "profits" and third-party names. BANNED_WORDS.md is the
definition list itself and must name every banned word.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LISTING = ROOT / "release" / "listing"
SHIPPED = [
    "README.md",
    "QUICKSTART.md",
    "TRUST.md",
    "SUPPORT.md",
    "CHANGELOG.md",
    "docs/ROADMAP.md",
]
EXEMPT_FILES = {"LICENSE", "NOTICE", "THIRD_PARTY.md", "BANNED_WORDS.md"}
# PowerShell's own parameter value; it is a command, not a claim.
ALLOWED_LINES = ("Set-ExecutionPolicy", "ExecutionPolicy Bypass")
BANNED = [
    "guaranteed",
    "free money",
    "passive income",
    "profit",
    "bypass",
    "sybil",
    "farm",
    "moon",
    "risk-free",
    "risk free",
    "get rich",
    "easy money",
    "guaranteed returns",
    "cannot lose",
    "no-brainer",
    "secret sauce",
]


def targets() -> list[Path]:
    files: list[Path] = []
    if LISTING.exists():
        files.extend(sorted(LISTING.rglob("*.md")))
    for name in SHIPPED:
        path = ROOT / name
        if path.exists():
            files.append(path)
    return files


def main() -> int:
    findings: list[str] = []
    for path in targets():
        if path.name in EXEMPT_FILES:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            if any(allowed.lower() in lowered for allowed in ALLOWED_LINES):
                continue
            for word in BANNED:
                if word in lowered:
                    findings.append(f"{path.relative_to(ROOT)}:{number}: {word!r}")
    if findings:
        print(f"FAIL: {len(findings)} hype word(s)")
        for finding in findings:
            print("  -", finding)
        return 1
    print(f"PASS: scanned {len(targets())} files; no banned marketing words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
