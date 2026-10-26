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

## D-014 — Process log module name and metrics scope

The logging helper is `idlescan/log.py`, not `logging.py`, so it can never
shadow the standard library. Process logs are optional and redacted; durable
audit records stay in SQLite. Request metrics are in-process counters exposed at
`GET /api/metrics` (behind the same bearer auth as every other `/api/*` route).
Paths are bucketed and capped so a hostile client cannot grow memory through
unique URLs; wallet addresses never appear in metric keys. `/api/health` reports
`status: degraded` when the database schema is not current but remains a
read-only probe (it never migrates).

`idlescan doctor` exits non-zero only for failures (invalid configuration,
unreadable chains/tokens, newer schema, corrupt data, unreachable RPC in
`--online` mode). A pending migration is a warning: it is actionable, not
corruption.

## D-015 — Alert conditions, escalation and resolution

Alert identity is `kind|wallet|drop|title[|tier]`, enforced by a partial unique
index over open rows. Deadlines carry an explicit tier (`14d`, `3d`), so a
condition escalates once when it becomes urgent; the older tier is resolved by
the same scan instead of leaving two open reminders.

After every scan, **condition** alerts (below gas, deadline, invalid proof,
claim error, missing dataset, missing eligibility) that are no longer true are
marked resolved for the drops evaluated by that scan. Event alerts (new
allocation, claim confirmed) are never swept: they describe something that
happened and stay until acknowledged or pruned. Resolution keeps the row for
history; `idlescan alerts --all` shows it.

Webhook notifications are opt-in via `IDLESCAN_NOTIFY_WEBHOOK_URLS`. Delivery
is fire-and-forget in a daemon thread (D-011), retried at most three times with
capped backoff, recorded per URL with a redacted URL and error, and never
allowed to fail a scan. `IDLESCAN_NOTIFY_DIGEST=true` sends one request per
scan instead of one per alert. Outbound webhook requests use the same SSRF
guard as dataset fetches.
