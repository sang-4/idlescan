# idlescan API Reference

All routes are under `/api/`. Responses are JSON with `cache-control: no-store`.
State-changing routes require `X-Idlescan: 1` header and, when `Origin` is present,
a matching host. When `IDLESCAN_AUTH_TOKEN` is set, all `/api/*` routes require
`Authorization: Bearer <token>`.

## Routes

### Health & Configuration

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/health` | Yes | Service health: DB schema, uptime, request stats, RPC health |
| GET | `/api/metrics` | Yes | Request counters (bucketed, capped), DB summary |
| GET | `/api/config` | Yes | Configuration summary: read-only, auth, rate limits, chains |
| GET | `/api/stats` | Yes | Service statistics (if implemented) |

### Wallets

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/wallets` | Yes | List tracked wallets |
| POST | `/api/wallets/bulk` | Yes | Bulk import (max 200 addresses) |
| GET | `/api/wallets/{address}` | Yes | Wallet detail (balances, claims, chains) |
| POST | `/api/wallets` | Yes | Add a tracked wallet |
| PATCH | `/api/wallets/{address}` | Yes | Update wallet label/tags |
| DELETE | `/api/wallets/{address}` | Yes | Stop tracking a wallet |

### Airdrops

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/airdrops` | Yes | List registry airdrops |
| GET | `/api/airdrops/{id}/export/{fmt}` | Yes | Export drop (json/csv/md) |
| GET | `/api/airdrops/{id}` | Yes | Airdrop detail (provenance, positions, history) |
| POST | `/api/airdrops` | Yes | Add a custom airdrop |
| PATCH | `/api/airdrops/{id}` | Yes | Update an airdrop |
| DELETE | `/api/airdrops/{id}` | Yes | Delete an airdrop |

### Scans & Jobs

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/scan` | Yes | Start a scan |
| GET | `/api/jobs` | Yes | List recent jobs |
| GET | `/api/jobs/{id}` | Yes | Get job detail |
| POST | `/api/jobs/{id}/cancel` | Yes | Request scan cancellation |

### Portfolio & Snapshots

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/portfolio` | Yes | Portfolio summary (optional `?snapshot=N`) |
| GET | `/api/snapshots` | Yes | List snapshots |
| GET | `/api/snapshots/{id}` | Yes | Snapshot detail |
| GET | `/api/snapshots/{id}/diff` | Yes | Side-by-side snapshot diff |

### Alerts

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/alerts` | Yes | List alerts (`?all=1` includes acknowledged/resolved) |
| POST | `/api/alerts/{id}/ack` | Yes | Acknowledge an alert |
| POST | `/api/alerts/{id}/resolve` | Yes | Resolve an alert condition |
| POST | `/api/alerts/{id}/reopen` | Yes | Reopen a resolved alert |
| POST | `/api/alerts/ack-all` | Yes | Acknowledge all open alerts |

### Claims

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/claims/{drop}/{wallet}/prepare` | Yes | Prepare unsigned claim (preflight first) |
| POST | `/api/claims/confirm` | Yes | Record claim confirmation (tx hash) |

### Export

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/export/{fmt}` | Yes | Export latest snapshot (json/csv/md) |

### Calendar

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/calendar.ics` | Yes | iCalendar feed of airdrop deadlines |

### Backup

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| POST | `/api/backup` | Yes | Write a consistent database backup |

### Utilities

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/api/search` | Yes | Search drops, wallets, transactions, pages |
| GET | `/api/analytics` | Yes | Analytics summary |
| POST | `/api/check` | Yes | Check one address without tracking (read-only) |
| GET | `/api/audit` | Yes | Audit log, executions, notification history |

## Security Headers

All responses include:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Resource-Policy: same-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`
- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`

## Error Format

```json
{
  "error": "human-readable message",
  "code": "error_code"
}
```

| Code | HTTP | Meaning |
|------|------|---------|
| `unauthorized` | 401 | Missing or invalid bearer token |
| `forbidden` / `read_only` / `lockdown` | 403 | Write refused |
| `not_found` | 404 | Resource not found |
| `conflict` | 409 | Concurrent scan, duplicate, or invalid state transition |
| `payload_too_large` | 413 | Request body exceeds limit |
| `unsupported_media_type` | 415 | Content-Type not application/json |
| `rate_limited` | 429 | Token bucket exhausted (Retry-After header) |
| `method_not_allowed` | 405 | HTTP method not supported on route |

## Request ID

Every API response includes `X-Request-Id`. Process logs include the same ID
when `IDLESCAN_HTTP_LOG=1`.

## Secret Redaction

RPC URLs with credentials, bearer tokens, and webhook URLs are redacted in all
error responses, audit entries, and log output.
