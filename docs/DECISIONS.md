# Decisions

Each entry records a choice made where the brief left room for interpretation.
The safer/more conservative option is preferred in every case.

## D-001 — Baseline facts differ from the brief

The workspace already contains a git repository with a configured remote, and the
suite contains 149 tests, not 148. A baseline tag `v1.1.0-baseline` and an empty
`chore: baseline v1.1.0` commit mark the starting point; no existing history was
rewritten.

## D-002 — Gas buffer applies to cost, not to the submitted gas limit

`IDLESCAN_GAS_BUFFER_PCT` inflates the *cost* used for net-value ranking (so the
product is conservative about profitability). The transaction's `gasLimit` gets a
separate fixed 25% headroom, which is a technical safety margin, not an economic
assumption. Both the raw and buffered costs are persisted and exported.

## D-003 — L1 fee on OP-stack chains is reported as `partial`

`GasPriceOracle.getL1Fee(bytes)` is called with the claim calldata. The predeploy
expects an RLP-encoded unsigned transaction; without a signer we cannot build the
exact envelope. The result is a conservative, clearly-labelled estimate, so the
quality is `partial` and a warning is emitted rather than claiming precision.

## D-004 — Payable claim functions are refused by default

A registry entry may declare `claim_payable: true`. Preparation refuses unless the
entry also sets `allow_payable: true`, and then returns `value: "0x0"` plus a
visible warning. No registry entry may silently require ETH.

## D-005 — Authentication token is stored in `sessionStorage`

The SPA keeps the bearer token in memory with a `sessionStorage` copy so a reload
does not force re-entry. It is never written to localStorage, a cookie, a URL, or
the audit log.

## D-006 — Rate limiting is off for loopback unless explicitly enabled

Defaults preserve the v1.1 local experience. Non-loopback clients are limited by
default; `IDLESCAN_RATE_LIMIT_LOOPBACK=true` opts a local user in.

## D-007 — Quorum defaults to 1

Quorum is an opt-in hardening feature. Preparing a claim uses quorum 2 whenever at
least two endpoints exist for the chain, because that is the highest-risk action.

## D-008 — `IDLESCAN_READ_ONLY` remains the canonical switch

User-facing language becomes "claim hand-off: enabled/disabled" and "local edits:
allowed/locked", but the environment variable and API field keep working. New
additive fields (`claim_handoff`, `local_edits`, `lockdown`) carry the clearer
names.

## D-009 — Registry coverage: tooling, not invented data

No distributor address, token address, or deadline was added from memory. WP-11
ships import/export, a JSON Schema, a contribution guide and health checks so
entries can be added with primary-source evidence. `docs/REGISTRY_EVIDENCE.md`
records that no new entries were verified during this session.

## D-010 — Large dataset indexing uses an on-disk SQLite side index

Above a size threshold, claim entries are written to a temporary SQLite index and
exposed through the existing `ClaimDataset` mapping interface. This bounds peak
memory; below the threshold the plain dict is faster and simpler.

## D-011 — Notifications are fire-and-forget with bounded retries

Webhook delivery is attempted outside the scan transaction. Failures are recorded
in audit/notification tables and never fail a scan. Three attempts with
exponential backoff, then a dead-letter state.

## D-012 — Migration backups are mandatory

Before applying any pending migration to an existing database, a
`<db>.pre-v<N>.bak` file is created and verified. If the backup cannot be written,
the database is not migrated and the process fails with an actionable message.

## D-013 — Retention defaults and dry-run pruning

`IDLESCAN_RETENTION_SNAPSHOTS=90`, alerts 180 days, audit 365 days, executions
365 days, jobs 30 days. `idlescan db prune` is a dry run unless `--yes` is
passed, the newest snapshot is always kept, and open alerts are never deleted.
`0` disables pruning for that category. Backups written to a directory are
rotated to the newest `IDLESCAN_BACKUP_KEEP` (default 5) files.
