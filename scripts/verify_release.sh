#!/usr/bin/env bash
# Buyer-simulation check for the release archive.
#
#   bash scripts/verify_release.sh [path/to/idlescan-desk-<version>.zip]
#
# Uses only the archive: nothing from the working tree is imported. Requires
# `curl` and either `python3 -m venv` or `virtualenv`.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
ARCHIVE=${1:-"$ROOT/release/dist/idlescan-desk-1.2.0.zip"}
WORK=${WORK:-"${TMPDIR:-/tmp}/idlescan-buyer"}
VERSION=$(python3 -c "import json;print(json.load(open('$ROOT/release/config.json'))['version'])")
NAME="idlescan-desk-$VERSION"

if [ ! -f "$ARCHIVE" ]; then
  echo "missing archive: $ARCHIVE" >&2
  exit 2
fi

rm -rf "$WORK"
mkdir -p "$WORK"
cd "$WORK"

echo "== 1. unzip =="
python3 -m zipfile -e "$ARCHIVE" .
cd "$NAME"
ls -1

echo
echo "== 2. verify CHECKSUMS.txt =="
python3 - <<'PY'
import hashlib, pathlib, sys
bad = []
for line in pathlib.Path("CHECKSUMS.txt").read_text().splitlines():
    if not line.strip():
        continue
    digest, name = line.split(None, 1)
    actual = hashlib.sha256(pathlib.Path(name.strip()).read_bytes()).hexdigest()
    if actual != digest:
        bad.append(name)
print("all files verified" if not bad else f"MISMATCH: {bad}")
sys.exit(1 if bad else 0)
PY

echo
echo "== 3. virtual environment and wheel install (no index, no deps) =="
if python3 -c "import ensurepip" 2>/dev/null && python3 -m venv .venv; then
  echo "created with python3 -m venv"
else
  echo "python3 -m venv unavailable (ensurepip missing); using virtualenv"
  rm -rf .venv
  virtualenv --quiet .venv
fi
.venv/bin/python -m pip install --no-index --no-deps --quiet "idlescan-$VERSION-py3-none-any.whl"
echo "install exit=$?"
.venv/bin/python -m pip show idlescan | sed -n '1,12p'

echo
echo "== 4. version, about, selftest =="
.venv/bin/idlescan --version
.venv/bin/idlescan about --json
.venv/bin/idlescan selftest | tail -3

echo
echo "== 5. demo server smoke test =="
IDLESCAN_DB="$WORK/buyer.db" .venv/bin/idlescan serve --demo --port 8877 > "$WORK/serve.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 40); do
  if curl -s -o /dev/null "http://127.0.0.1:8877/api/health"; then break; fi
  sleep 0.25
done
for path in /api/health / /api/portfolio /api/airdrops /app.js; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8877$path")
  echo "GET $path -> $code"
done
echo "-- security headers on / --"
curl -s -D - -o /dev/null "http://127.0.0.1:8877/" | tr -d '\r' | grep -Ei '^(content-security-policy|x-content-type-options|x-frame-options|referrer-policy|permissions-policy):'
echo "-- health payload --"
curl -s "http://127.0.0.1:8877/api/health"
echo
kill "$SERVER_PID" 2>/dev/null
wait "$SERVER_PID" 2>/dev/null
echo "-- server log --"
cat "$WORK/serve.log"

echo
echo "== 6. remote bind refused =="
IDLESCAN_DB="$WORK/buyer.db" .venv/bin/idlescan serve --host 0.0.0.0 --port 8878
echo "exit code=$?"

echo
echo "== 7. wheel/sdist contents check =="
python3 - <<PY
import tarfile, zipfile
for name in ("idlescan-$VERSION-py3-none-any.whl", "idlescan-$VERSION.tar.gz"):
    if name.endswith(".whl"):
        names = zipfile.ZipFile(name).namelist()
    else:
        names = tarfile.open(name).getnames()
    bad = [n for n in names if "/tests/" in n or n.startswith("tests/") or "fixtures/" in n]
    print(f"{name}: {len(names)} entries, tests/fixtures={bad or 'none'}")
PY
