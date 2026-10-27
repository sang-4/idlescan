# Claims evidence

Every factual claim in `LISTING.md`, with the file or command that proves it.
Counts were taken from the release tree on 2026-10-27.

| Claim | Evidence |
| --- | --- |
| Product name, version 1.2.0 | `release/config.json`; `pyproject.toml` `version = "1.2.0"`; `idlescan/__init__.py` |
| Seller is Allan Kiprop, https://allank.dev | `release/config.json`; `LICENSE`; `pyproject.toml` authors/URLs |
| Zero runtime dependencies | `pyproject.toml` has no `[project].dependencies`; `pip show idlescan` prints empty `Requires:` (see `release/VERIFICATION.md`) |
| 14 EVM chains | `python -c "import json; print(len(json.load(open('idlescan/data/chains.json'))['chains']))"` → 14 |
| 58 curated tokens | `python -c "import json; d=json.load(open('idlescan/data/tokens.json')); print(sum(len(v) for v in d['chains'].values()))"` → 58 |
| 38 API routes | `grep -c 'router.add(' idlescan/api.py` → 38 |
| 18 top-level CLI commands | `python -m idlescan --help` lists 18; `grep -o 'sub.add_parser("[a-z]*"' idlescan/cli.py` |
| 8 main views plus drop and wallet profiles | `ROUTES` in `idlescan/web/app.js` (overview, check, drops, claims, wallets, alerts, activity, settings) plus `#/drop/<id>` and `#/wallet/<address>` |
| Merkle proofs checked against on-chain root | `idlescan/verify.py`; `tests/test_merkle.py`; `tests/test_claim_modes.py` |
| EIP-1559-aware fees, buffer, L1 fee where available, quality label | `idlescan/gas.py`; `tests/test_gas.py`; `gas_estimate_quality` in `idlescan/report.py` |
| Gas note: estimate may exclude L2 data fees | `GAS_ESTIMATE_NOTE` in `idlescan/gas.py`; shown in CLI, exports, UI, README |
| Immutable snapshots and diffs | `idlescan/db.py` snapshots; `GET /api/snapshots/{id}/diff` in `idlescan/api.py`; `tests/test_service.py` |
| Alerts for deadline/below-gas/invalid proof/new allocation/confirmed claim/missing dataset | `idlescan/service.py` alert creation; `tests/test_alerts_v2.py` |
| JSON/CSV/Markdown exports | `idlescan/report.py`; `GET /api/export/{fmt}`; `tests/test_report.py` |
| iCalendar deadline feed | `GET /api/calendar.ics`; `tests/test_api.py` |
| Bearer auth, rate limits, body limits, lockdown, security headers | `idlescan/api.py`; `idlescan/config.py`; `tests/test_security.py`; `tests/test_api.py` |
| SSRF-safe fetcher: HTTPS only, private/metadata blocked, redirects re-checked, size/time caps | `idlescan/safe_http.py`; `tests/test_safe_http.py` |
| Streaming reads and provenance for large datasets | `idlescan/dataset.py`; `tests/test_provenance.py` |
| Migrations with pre-migration backup, retention, backup rotation | `idlescan/migrations.py`; `idlescan/db.py`; `tests/test_migrations.py` |
| Structured redacted logs, `doctor`, `about` | `idlescan/log.py`; `idlescan/doctor.py`; `idlescan/about.py`; `tests/test_observability.py`; `tests/test_about.py` |
| Demo mode and offline self-test | `idlescan/selftest.py`; `python -m idlescan selftest` |
| One bundled registry entry (Uniswap UNI), no bundled dataset | `idlescan/data/airdrops.json` has one entry; `idlescan/data/datasets/` contains only `README.md` |
| Read-only default; optional unsigned hand-off | `idlescan/config.py` `read_only: bool = True`; `idlescan/service.py` prepare/confirm gating; `tests/test_claim_modes.py` |
| Loopback default; non-loopback refused without token or override | `idlescan/config.py` `is_loopback` / `validate`; `tests/test_config.py` |
| No key handling, signing or broadcasting | `python -c` audit in `TRUST.md` prints "no signing or key-handling symbols found" (recorded in `release/VERIFICATION.md`) |
| Test suite passes offline | `python -m unittest discover -s tests` (count in `release/VERIFICATION.md`) |
| Python 3.10+ | `requires-python = ">=3.10"` in `pyproject.toml` |
| Verified on Linux x86-64 with Python 3.12 | `release/VERIFICATION.md` |
| 14-day refund window | `release/config.json` `refund_days: 14`; `LICENSE` section 10; `SUPPORT.md` |
| Screenshots are the owner's task | No headless browser was available; see `release/RELEASE_REPORT.md` |
