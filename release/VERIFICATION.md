# Fresh-machine verification

Command:

```bash
bash scripts/verify_release.sh
```

Environment: Linux 6.18 (WSL2), x86-64, CPython 3.12.3, `virtualenv` 21.6.1.
The archive is `release/dist/idlescan-desk-1.2.0.zip`; its final SHA-256 is in
`release/dist/idlescan-desk-1.2.0.zip.sha256` and is the value to publish.

Nothing from the working tree is imported: the wheel is installed from the
archive into a fresh environment, and the server runs from that environment with
a database outside the repository.

## Real output

```text
== 1. unzip ==
CHANGELOG.md
CHECKSUMS.txt
LICENSE
NOTICE
QUICKSTART.md
README.md
START_HERE.txt
SUPPORT.md
THIRD_PARTY.md
TRUST.md
examples
idlescan-1.2.0-py3-none-any.whl
idlescan-1.2.0.tar.gz

== 2. verify CHECKSUMS.txt ==
all files verified

== 3. virtual environment and wheel install (no index, no deps) ==
python3 -m venv unavailable (ensurepip missing); using virtualenv
install exit=0
Name: idlescan
Version: 1.2.0
Summary: Self-hosted airdrop research desk: idle balances, unclaimed allocations and gas-adjusted claim planning (read-only by default)
Home-page: https://allank.dev
Author:
Author-email: Allan Kiprop <support@allank.dev>
License: Proprietary
Location: /tmp/idlescan-buyer/idlescan-desk-1.2.0/.venv/lib/python3.12/site-packages
Requires:
Required-by:

== 4. version, about, selftest ==
idlescan 1.2.0
{
  "auth_required": false,
  "chains_configured": 14,
  "chains_enabled": 14,
  "claim_handoff": "disabled",
  "database": "idlescan.db",
  "database_exists": false,
  "dataset_dir_configured": false,
  "dataset_dir_present": false,
  "demo_mode": false,
  "license": "Proprietary single-user license (see LICENSE)",
  "os": "Linux 6.18.54.1-microsoft-standard-WSL2 (x86_64)",
  "platform": "linux",
  "product": "idlescan Desk",
  "python": "3.12.3",
  "python_implementation": "CPython",
  "rate_limit_enabled": false,
  "read_only": true,
  "remote_guard": "loopback-only",
  "schema_version": "not created",
  "version": "1.2.0"
}
  - gas estimate present: True

This demo used a mock chain with zero network calls.

== 5. demo server smoke test ==
GET /api/health -> 200
GET / -> 200
GET /api/portfolio -> 200
GET /api/airdrops -> 200
GET /app.js -> 200
-- security headers on / --
x-content-type-options: nosniff
x-frame-options: DENY
referrer-policy: no-referrer
permissions-policy: camera=(), microphone=(), geolocation=(), payment=()
content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'
-- health payload --
{"status": "ok", "version": "1.2.0", "uptime_seconds": 0.377, "read_only": true, "claim_handoff": false, "local_edits": true, "lockdown": false, "database": {"schema_version": 2, "latest_schema_version": 2, "pending_migrations": 0, "up_to_date": true, "size_bytes": 131072}, "requests": {"total": 7, "in_flight": 1, "errors": 0, "unauthorized": 0, "rate_limited": 0, "overflowed_paths": 0, "last_status": 200, "last_at": "2026-10-27T09:26:45+00:00", "by_method": {"GET": 7}, "by_status": {"2xx": 7}}, "rpc": []}
-- server log --
2026-10-27 02:26:45,268 INFO idlescan.api listening on http://127.0.0.1:8877/ (read_only=True)
idlescan 1.2.0  ·  http://127.0.0.1:8877
claim hand-off: disabled   local edits: allowed   db: /tmp/idlescan-buyer/buyer.db
auth: none (loopback only)
log:  INFO text
press ctrl-c to stop

== 6. remote bind refused ==
error: refusing to start on non-loopback host 0.0.0.0 without IDLESCAN_AUTH_TOKEN. Set a token (recommended) or explicitly accept the risk with IDLESCAN_ALLOW_INSECURE_REMOTE=true.
exit code=2

== 7. wheel/sdist contents check ==
idlescan-1.2.0-py3-none-any.whl: 45 entries, tests/fixtures=none
idlescan-1.2.0.tar.gz: 63 entries, tests/fixtures=none
```

## What was and was not covered

Covered:

- Checksums of every shipped file.
- A clean environment built only from the archive, with no index access and no
  dependency resolution (`pip show` confirms an empty `Requires:`).
- `--version`, `about --json`, offline `selftest`.
- Demo server on an explicit port, with 200 responses for the five endpoints a
  buyer touches first, plus the full security-header set.
- Remote bind refusal with exit code 2.
- Absence of `tests/` and the GPL fixture from both distributions.

Not covered, and therefore not claimed:

- **Python 3.10, 3.11 and 3.13.** Only 3.12.3 is installed on this machine. The
  package declares `>=3.10`; the syntax is 3.10-compatible by inspection, not by
  execution.
- **Windows and macOS.** Commands are documented for all three platforms, but the
  release was executed on Linux (WSL2) only.
- **A real RPC scan.** The verification uses demo mode and the mock-chain
  self-test, so it proves the pipeline runs, not that a specific provider works.
- **The browser UI in a real browser.** No browser is installed; the HTML, JS and
  CSS were fetched over HTTP and the API responses checked, but screenshots and
  interactive behaviour are the owner's capture task.

## How the archive itself was checked

```text
$ python scripts/scan_release.py
PASS: no secrets, personal paths or banned words in idlescan-desk-1.2.0.zip

$ python scripts/check_copy.py
PASS: scanned 11 files; no banned marketing words
```

The build script also refuses to finish if an excluded file, the support-email
placeholder, or the banned seller string appears in the archive, and it verifies
that neither distribution contains `tests/` or the GPL fixture.
