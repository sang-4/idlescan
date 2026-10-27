#!/usr/bin/env python3
"""Build the sellable idlescan Desk release archive.

Standard library only.  Produces:

    release/dist/idlescan-desk-<version>.zip
    release/dist/idlescan-desk-<version>.zip.sha256

The archive is deterministic (sorted entries, fixed timestamps) and the script
exits non-zero if an excluded file, the support-email placeholder, or the banned
seller string is present.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "release"
BUILD = RELEASE / "build"
DIST = RELEASE / "dist"
CONFIG_PATH = RELEASE / "config.json"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)
BANNED = ("xtanol",)
PLACEHOLDER = "SUPPORT_EMAIL_HERE"

DOC_FILES = [
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY.md",
    "README.md",
    "QUICKSTART.md",
    "TRUST.md",
    "SUPPORT.md",
    "CHANGELOG.md",
]

REQUIRED_IN_ARCHIVE = DOC_FILES + [
    "START_HERE.txt",
    "CHECKSUMS.txt",
    "examples/airdrops.example.json",
    "examples/.env.example",
]

ENV_EXAMPLE = """\
# idlescan Desk - example configuration. Rename to .env and edit.
# Every value is optional. Read-only is the safe default.
IDLESCAN_READ_ONLY=true

# Loopback only. A non-loopback bind requires IDLESCAN_AUTH_TOKEN.
IDLESCAN_HOST=127.0.0.1
IDLESCAN_PORT=8787

# Absolute path recommended: the SQLite database lives here.
# IDLESCAN_DB=~/idlescan-data/idlescan.db
IDLESCAN_DB=data/idlescan.db

# Optional: a provider URL you are entitled to use.
# IDLESCAN_RPC_ETHEREUM=https://ethereum-rpc.publicnode.com

# Optional: writable folder for downloaded claim datasets.
# IDLESCAN_DATASET_DIR=~/idlescan-data/datasets

# Optional: require Authorization: Bearer <token> on every API call.
# IDLESCAN_AUTH_TOKEN=change-me

# Claims hand-off stays off unless you set this to false.
IDLESCAN_READ_ONLY=true
"""

START_HERE = """\
idlescan Desk {version} - {release_name}
==================================================

What is in this folder:
  idlescan-{version}-py3-none-any.whl   the product (install this)
  idlescan-{version}.tar.gz             source, for reading and auditing
  README.md, QUICKSTART.md              start with QUICKSTART.md
  TRUST.md, SUPPORT.md, LICENSE          guarantees, help, terms
  CHECKSUMS.txt                          SHA-256 of every other file

Three commands to get running (Python 3.10+):

  1. Verify:  compare the .zip hash on the product page, then run the
              CHECKSUMS.txt snippet in QUICKSTART.md section 3.
  2. Install: python -m venv .venv
              (activate it) then: python -m pip install idlescan-{version}-py3-none-any.whl
  3. Check:   idlescan selftest

Then:  idlescan serve --demo     -> http://127.0.0.1:8787

Support: {support}
This is a research tool, not financial advice.
"""


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"missing {CONFIG_PATH}")
    config = json.loads(CONFIG_PATH.read_text())
    email = str(config.get("support_email") or "").strip()
    if not email or PLACEHOLDER in email:
        raise SystemExit("release/config.json must contain a real support_email")
    if "@" not in email:
        raise SystemExit(f"support_email looks invalid: {email!r}")
    if not config.get("version"):
        raise SystemExit("release/config.json must contain a version")
    return config


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}")


def build_artefacts(version: str) -> tuple[Path, Path]:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    if importlib.util.find_spec("build") is None:
        print("python -m build not available; using pip wheel + source tarball")
        run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(BUILD), str(ROOT)])
        wheel = next(BUILD.glob(f"idlescan-{version}-*.whl"), None)
        if wheel is None:
            raise SystemExit("pip wheel did not produce a wheel") from None
        sdist = _fallback_sdist(version)
        return wheel, sdist
    run([sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(BUILD)])
    wheel = next(BUILD.glob(f"idlescan-{version}-*.whl"), None)
    sdist = BUILD / f"idlescan-{version}.tar.gz"
    if wheel is None or not sdist.exists():
        raise SystemExit("build did not produce the expected wheel and sdist")
    return wheel, sdist


def _source_files() -> list[Path]:
    files = [ROOT / name for name in DOC_FILES + ["pyproject.toml", "MANIFEST.in", "py.typed"]]
    for path in sorted((ROOT / "idlescan").rglob("*")):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".db"}:
            continue
        if path.is_file():
            files.append(path)
    return [path for path in files if path.exists()]


def _fallback_sdist(version: str) -> Path:
    target = BUILD / f"idlescan-{version}.tar.gz"
    prefix = f"idlescan-{version}"
    with tarfile.open(target, "w:gz") as archive:
        for path in _source_files():
            arcname = f"{prefix}/{path.relative_to(ROOT).as_posix()}"
            info = archive.gettarinfo(str(path), arcname)
            info.mtime = 1767225600  # 2026-01-01T00:00:00Z
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    return target


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def stage_archive(config: dict, wheel: Path, sdist: Path) -> Path:
    version = config["version"]
    stage = BUILD / f"idlescan-desk-{version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for name in DOC_FILES:
        source = ROOT / name
        if not source.exists():
            raise SystemExit(f"missing required document: {name}")
        text = source.read_text(encoding="utf-8")
        if PLACEHOLDER in text:
            raise SystemExit(f"{name} still contains {PLACEHOLDER}")
        _write(stage / name, text)

    _write(stage / "START_HERE.txt", START_HERE.format(
        version=version,
        release_name=config.get("release_name", "Release"),
        support=config["support_email"],
    ))
    _write(stage / "examples" / ".env.example", ENV_EXAMPLE)
    shutil.copy2(ROOT / "examples" / "airdrops.example.json", stage / "examples" / "airdrops.example.json")
    for artefact in (wheel, sdist):
        shutil.copy2(artefact, stage / artefact.name)
    return stage


def write_checksums(stage: Path) -> None:
    rows = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file() or path.name == "CHECKSUMS.txt":
            continue
        relative = path.relative_to(stage).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {relative}")
    _write(stage / "CHECKSUMS.txt", "\n".join(rows) + "\n")


def _forbidden(name: str) -> str | None:
    lowered = name.lower()
    checks = [
        ("/tests/", "tests directory"),
        ("/fixtures/", "test fixture"),
        ("__pycache__", "bytecode cache"),
        (".pyc", "bytecode"),
        (".db", "database"),
        ("/data/datasets/", "claim dataset"),
        ("/.git/", "git metadata"),
        ("/release/", "release tooling"),
        ("project_analysis.md", "internal analysis"),
        (".ds_store", "editor/OS file"),
        (".vscode/", "editor file"),
        (".idea/", "editor file"),
        (".swp", "editor file"),
    ]
    for needle, reason in checks:
        if needle in lowered and not lowered.endswith(".env.example"):
            return reason
    base = lowered.rsplit("/", 1)[-1]
    if base == ".env":
        return "environment file"
    return None


def validate(stage: Path, archive: Path, config: dict) -> None:
    version = config["version"]
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        prefix = f"idlescan-desk-{version}/"
        if sorted(names) != names:
            raise SystemExit("archive entries are not sorted")
        for name in names:
            if not name.startswith(prefix):
                raise SystemExit(f"entry outside release folder: {name}")
            reason = _forbidden(name)
            if reason:
                raise SystemExit(f"forbidden {reason} in archive: {name}")
            data = zf.read(name)
            if PLACEHOLDER.encode() in data:
                raise SystemExit(f"support-email placeholder in {name}")
            for word in BANNED:
                if word.encode() in data.lower():
                    raise SystemExit(f"banned word {word!r} in {name}")
        relative = {name[len(prefix):] for name in names}
        for required in REQUIRED_IN_ARCHIVE:
            if required not in relative:
                raise SystemExit(f"missing required file: {required}")
        wheels = [name for name in relative if name.endswith(".whl")]
        sdists = [name for name in relative if name.endswith(".tar.gz")]
        if len(wheels) != 1 or len(sdists) != 1:
            raise SystemExit("archive must contain exactly one wheel and one sdist")

    _assert_no_tests(stage / f"idlescan-{version}-py3-none-any.whl")
    _assert_no_tests(stage / f"idlescan-{version}.tar.gz")

    for path in stage.rglob("*"):
        if path.is_file():
            text = path.read_bytes().lower()
            if PLACEHOLDER.encode() in text:
                raise SystemExit(f"support-email placeholder in {path.name}")
            for word in BANNED:
                if word.encode() in text:
                    raise SystemExit(f"banned word {word!r} in {path.name}")


def _assert_no_tests(path: Path) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
    for name in names:
        lowered = name.lower()
        if "/tests/" in lowered or lowered.startswith("tests/") or "fixtures/" in lowered:
            raise SystemExit(f"distribution contains tests or fixtures: {name}")


def make_zip(stage: Path, version: str) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    archive = DIST / f"idlescan-desk-{version}.zip"
    if archive.exists():
        archive.unlink()
    files = sorted(path for path in stage.rglob("*") if path.is_file())
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            arcname = f"{stage.name}/{path.relative_to(stage).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            info.create_system = 3
            zf.writestr(info, path.read_bytes())
    return archive


def main() -> int:
    config = load_config()
    version = str(config["version"])
    print(f"building {config['product_name']} {version}")
    wheel, sdist = build_artefacts(version)
    print(f"wheel: {wheel.name}")
    print(f"sdist: {sdist.name}")
    stage = stage_archive(config, wheel, sdist)
    write_checksums(stage)
    archive = make_zip(stage, version)
    validate(stage, archive, config)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (DIST / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"archive: {archive} ({archive.stat().st_size:,} bytes)")
    print(f"sha256:  {digest}")
    print("contents:")
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            print("  ", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
