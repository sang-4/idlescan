# idlescan Changelog

## [1.2.0] — 2026-10-26

### Added

- **WP-01 Gas model** (`idlescan/gas.py`): per-chain `fee_model` (eip1559/legacy) and `l1_fee`
  metadata from `chains.json`; `gas_estimate_quality` (full/partial/unavailable) on every claim;
  EIP-1559 `maxFeePerGas`/`maxPriorityFeePerGas` in `claims prepare`; configurable gas buffer
  (`IDLESCAN_GAS_BUFFER_PCT`, default 20%) applied to net value with raw and buffered figures shown
  separately; unknown-net claims ranked in their own group.
- **WP-02 Claim preflight**: fresh on-chain preflight at claim prepare time (re-read
  `isClaimed`/`claimed`, `merkleRoot()`, distributor code, simulated call); calldata integrity
  check (selector, arguments, proof); `value: "0x0"` assertion; payable claims refused unless
  `claim_payable` + `allow_payable`; preflight results recorded in executions/audit.
- **WP-03 SSRF-safe fetching** (`idlescan/safe_http.py`): HTTPS-only, DNS validation, private/loopback/
  link-local/multicast/reserved IP blocking, redirect re-validation (max 3), response size cap
  (`IDLESCAN_MAX_FETCH_BYTES`), content-type allowlist, optional host allowlist
  (`IDLESCAN_FETCH_ALLOWED_HOSTS`); plain HTTP/private opt-in via `IDLESCAN_ALLOW_HTTP_FETCH`.
- **WP-04 Access control**: bearer token auth (`IDLESCAN_AUTH_TOKEN`), startup refusal on non-loopback
  without token, per-client token-bucket rate limits, 413/415/405/429 responses, `IDLESCAN_LOCKDOWN`,
  plain-language "claim hand-off" / "local edits" labels, `IDLESCAN_READ_ONLY` alias.
- **WP-05 Quorum reads**: `IDLESCAN_RPC_QUORUM` (default 1); critical reads must agree across N
  endpoints; preflight always uses quorum 2; per-endpoint health tracking; RPC credentials redacted
  everywhere.
- **WP-06 Migrations** (`idlescan/migrations.py`): ordered numbered migrations, automatic pre-migration
  backup, newer-schema refusal, `idlescan db status|migrate|check|prune`, retention pruning
  (dry-run by default), backup rotation (`IDLESCAN_BACKUP_KEEP`).
- **WP-07 Dataset provenance** (`idlescan/dataset.py`): streaming JSON parser for large claim files;
  on-disk SQLite index above threshold; provenance records (sha256, size, file date, root
  verification, block number); staleness warnings (`IDLESCAN_DATASET_STALE_DAYS`); provenance in
  profiles, reports, and `idlescan dataset` CLI.
- **WP-08 Observability** (`idlescan/doctor.py`, `idlescan/log.py`): `idlescan doctor --offline|--online`
  with JSON output; redacted structured logs (`IDLESCAN_LOG_FORMAT=json|text`); `GET /api/metrics`
  with bucketed request counters; extended `GET /api/health` with DB schema, uptime, request stats.
- **WP-09 Alerts**: condition scopes with escalation tiers (deadline 14d→3d), auto-resolution of
  conditions that no longer hold, event alerts (new allocation, claim confirmed) never swept,
  opt-in webhook notifications with bounded retries and dead-letter, redacted URLs, `alerts
  --resolve/--reopen` CLI and API endpoints.
- **WP-10 Deployment**: `Dockerfile` (slim, non-root, HEALTHCHECK), `docker-compose.yml`,
  `deploy/idlescan.service` (hardened systemd), `.github/workflows/ci.yml`, `Makefile`, `py.typed`,
  dev-only dependencies under `[project.optional-dependencies].dev`.
- **WP-11 Registry tooling**: `idlescan registry import|export`, `idlescan/data/registry.schema.json`,
  `docs/REGISTRY_CONTRIBUTING.md`; no fabricated entries.
- **WP-12 Custom tokens**: `idlescan tokens list|add|rm`; custom tokens persisted in DB, surface
  alongside curated tokens; add/remove require local edits and write mode.
- **WP-13 Scan engine**: concurrent chain scanning (`IDLESCAN_SCAN_WORKERS`), cooperative
  cancellation (`POST /api/jobs/{id}/cancel`), scan timeout (`IDLESCAN_SCAN_TIMEOUT_SECONDS`),
  phase timings, startup recovery for stale jobs, single-flight scan lock.

### Changed

- `idlescan doctor` exits 1 when there are warnings (not just failures).
- `keccak.py` docstring test file reference corrected (`test_keccak_abi.py`).
- `ScanReport` gains `phase_timings` and `datasets` fields.
- Demo/test net values updated from `607.007376` to `606.508114` (buffered gas).
- Startup warning text: "CLAIM HAND-OFF ENABLED".
- Remote startup requires auth token unless `IDLESCAN_ALLOW_INSECURE_REMOTE=true`.

### Removed

- None (all changes are additive).

### Deprecated

- `IDLESCAN_READ_ONLY` remains supported as an alias for the read-only switch.

### Fixed

- Single-leaf merkle proof-mode detection (empty proof is verifiable).
- Amount parsing errors for non-integer decimal strings are now named with the address.
- `idlescan doctor` no longer modifies the database (read-only checks).
- **Server startup crash**: `idlescan serve` crashed with `ValueError: Port could not be cast to integer value as '%s'` because the log redaction filter redacted the printf-style format string (`http://%s:%s`) before arguments were applied, mistaking the format placeholder for a URL with an invalid port. The filter now formats the message before redacting (`idlescan/log.py`), and `redact_url` is defensive against non-integer ports (`idlescan/redaction.py`).

### Security

- All RPC URLs, bearer tokens, and webhook URLs are redacted in logs, errors, audit, and API
  responses.
- Webhook URLs are validated through the SSRF-safe client.
- Paths in alert bodies are reduced to filenames on read.
