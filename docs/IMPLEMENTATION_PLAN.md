# idlescan v1.2.0 implementation plan

Baseline: v1.1.0, 149 offline tests, self-test green, schema version 1.
Work is ordered by priority; a package is only committed when the full suite is green.

| WP | Priority | Scope | Primary files | Tests | Main risks |
| --- | --- | --- | --- | --- | --- |
| 01 | P0 | Labeled gas model, EIP-1559/legacy fees, L1 fee, buffer, quality | `gas.py` (new), `chains.py`, `scanner.py`, `service.py`, `db.py`, `report.py`, `web/*` | `test_gas.py` (new), scanner/service/API additions | fee math drift, breaking exports |
| 02 | P0 | Preflight + calldata integrity | `service.py`, `abi.py`, `api.py`, `web/app.js` | `test_preflight.py` (new) | fresh RPC reads in tests |
| 03 | P0 | SSRF-safe outbound HTTP | `safe_http.py` (new), `scanner.py`, `prices.py`, `service.py` | `test_safe_http.py` (new) | DNS/SNI handling, redirects |
| 04 | P0 | Auth, rate limits, body/content limits, lockdown, honest labels | `config.py`, `api.py`, `service.py`, `web/*` | `test_security.py` (new) | not breaking loopback defaults |
| 05 | P0 | Quorum reads, endpoint health, secret redaction | `rpc.py`, `health.py` (new), `redaction.py` (new), `scanner.py`, `api.py`, `cli.py` | `test_quorum.py` (new) | quorum semantics with failover |
| 06 | P1 | Migration framework, backups, newer-schema refusal, prune | `db.py`, `migrations.py` (new), `cli.py` | `test_migrations.py` (new) | data loss on v1 DBs |
| 07 | P1 | Dataset provenance, fetch, staleness, streaming parse | `service.py`, `registry.py`, `merkle.py`, `cli.py`, `web/*`, docs | `test_provenance.py` (new) | large-file memory |
| 08 | P1 | doctor, structured logs, health, metrics | `doctor.py` (new), `log.py` (new), `api.py`, `cli.py` | `test_doctor.py`, `test_observability.py` | offline safety |
| 09 | P1 | Alert dedupe scopes, escalation, resolution, notifications | `db.py`, `service.py`, `notifications.py` (new), `cli.py`, `web/*` | `test_alerts_v2.py` (new) | retry loops, secret exposure |
| 10 | P1 | Containers, CI, dev tooling, Makefile | `Dockerfile`, `compose.yaml`, `deploy/`, `.github/`, `pyproject.toml` | config-parse tests | unavailable toolchain |
| 11 | P2 | Registry verification tooling and schema | `registry.py`, `verify.py`, `cli.py`, `registry.schema.json`, docs | `test_registry_tools.py` | fabrication risk (avoided) |
| 12 | P2 | Custom tokens, token lists, Multicall3, discovery | `tokens.py` (new), `db.py`, `scanner.py`, `service.py`, `cli.py`, `web/*` | `test_tokens.py` (new) | spam heuristics |
| 13 | P2 | Concurrent scans, cancellation, timeouts, pricing confidence | `scanner.py`, `service.py`, `api.py`, `prices.py` | `test_jobs_v2.py` (new) | thread safety |
| 14 | P2 | ES modules, new UI surfaces, themes, a11y | `web/js/*`, `web/index.html`, `web/app.css` | node tests + asset tests | large mechanical split |
| 15 | P2 | Documentation, drift fixes, doc-sync tests | `README.md`, `docs/*`, `SECURITY.md`, etc. | `test_docs.py` (new) | route/env drift |
| 16 | P2 | Property, adversarial, end-to-end tests, coverage | `tests/*` | many | runtime budget |

## Cross-cutting decisions

- Public APIs and DB columns are additive. Old snapshot rows without gas metadata render as "unavailable" rather than zero.
- Any feature that widens exposure (auth, notifications, HTTP fetch, discovery) defaults off.
- New modules are typed; existing modules gain types on touched public functions.
- Doc-sync tests derive route and env-var lists from code so documentation cannot silently drift.
