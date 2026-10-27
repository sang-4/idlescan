# idlescan Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.2.0   | ✅        |
| 1.1.0   | ❌        |

## Security Overview

idlescan is a **non-custodial, read-by-default** airdrop research desk. The server:

- **Never holds keys or seed phrases**
- **Never signs transactions**
- **Never broadcasts transactions**
- **Never requests off-chain signatures or approvals**

Claim hand-off returns an **unsigned** transaction for the user's own browser wallet to review
and sign. The user must verify every transaction before approving it.

## Trust Boundaries

### Trusted

- The operator running the idlescan instance
- The local SQLite database file (not encrypted at rest by idlescan)
- Bundled chain/RPC configuration and token lists
- Registry entries vetted by the operator

### Not Trusted

- Third-party RPC endpoints (verified via quorum reads, fail closed)
- Third-party claim datasets (proofs verified against on-chain root)
- User-supplied URLs (dataset URLs, webhook URLs — validated via SSRF-safe client)
- API input from untrusted clients

## Guarantees

| Guarantee | Implementation |
|-----------|---------------|
| Zero runtime dependencies | Standard library only |
| No keys stored | No key material anywhere in code or DB |
| No signing/broadcasting | Server only constructs unsigned claim payloads |
| Proof-first trust | Merkle proofs hash-verified against on-chain root before reporting |
| SSRF protection | `safe_http.py` blocks private/loopback/reserved IPs; HTTPS-only by default |
| Secret redaction | RPC URLs, bearer tokens, webhook URLs redacted in logs, errors, audit, API |
| Rate limiting | Token buckets per client/IP; 429 with Retry-After |
| Read-only defaults | `IDLESCAN_READ_ONLY=true`, loopback bind, no auth required locally |
| Fail-closed quorum | One endpoint erroring cannot satisfy quorum of 2 |

## Configuration Security

### `IDLESCAN_AUTH_TOKEN`

Bearer token required for all `/api/*` requests when set. Generate with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### `IDLESCAN_ALLOW_INSECURE_REMOTE`

Allows non-loopback bind without a token. Logs a prominent warning and shows a persistent
UI banner. **Not recommended for production.**

### `IDLESCAN_ALLOW_HTTP_FETCH`

Allows plain HTTP and private addresses for outbound fetches. **Off by default.** Only enable
for local fixture testing.

### `IDLESCAN_LOCKDOWN`

Disables all local administrative writes (wallets, airdrops, alerts, backups, scans). Reads,
exports, and `POST /api/check` still work.

## Reporting Security Issues

Please report security concerns through the project's issue tracker. Do not disclose
vulnerabilities publicly until they have been addressed.

## Audit Log

All administrative actions, scans, claim preparations, alert acknowledgements, and notification
attempts are recorded in the SQLite `audit` table with redacted details. Audit entries are
append-only and retained according to `IDLESCAN_RETENTION_AUDIT_DAYS` (default 365).

## Threat Model

| Threat | Mitigation |
|--------|-----------|
| Malicious RPC returns wrong root/state | Quorum reads across multiple endpoints; fail closed |
| SSRF via dataset/webhook URLs | IP allow/block lists, HTTPS-only, redirect re-validation |
| Credential leakage in logs | Redaction filter on all log output |
| Unauthorized API access | Bearer token auth; startup refusal on remote without token |
| Stale claim data | Preflight re-reads chain state before returning claim |
| Database corruption | Migrations in transactions; integrity checks; pre-migration backups |
