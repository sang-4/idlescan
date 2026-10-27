# idlescan Configuration

Every environment variable is optional. Values in `.env` are loaded from `./.env` or the project
root. Command-line flags (`--env-file`, `--db`, `--host`, `--port`) override environment values.

## Core

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_READ_ONLY` | `true` | Read-only mode. `false` enables claim hand-off (unsigned transactions). Alias for write mode. |
| `IDLESCAN_HOST` | `127.0.0.1` | Bind address. Non-loopback requires `IDLESCAN_AUTH_TOKEN`. |
| `IDLESCAN_PORT` | `8787` | TCP port. |
| `IDLESCAN_DB` | `data/idlescan.db` | SQLite database path. Relative paths resolve against project root. |
| `IDLESCAN_CHAINS` | (all) | Comma-separated chain keys to scan; empty means all configured chains. |
| `IDLESCAN_MIN_USD` | `1.0` | Hide balances below this USD value. |
| `IDLESCAN_REFRESH_MINUTES` | `0` | Auto-rescan interval in minutes; 0 = manual only. |
| `IDLESCAN_DEMO` | `false` | Seed a synthetic demo portfolio on startup (no network). Demo mode uses `idlescan-demo.db` beside the configured database unless `--db` is passed; turning it off removes leftover demo rows. |
| `IDLESCAN_DATASET_DIR` | (none) | Extra directory to search for merkle claim datasets. |

## Safety: Claim Hand-off, Authentication, Remote Access

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_AUTH_TOKEN` | (none) | Bearer token required for every `/api/*` request. Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`. |
| `IDLESCAN_ALLOW_INSECURE_REMOTE` | `false` | Allow non-loopback bind without a token. Logs a warning; persistent UI banner. |
| `IDLESCAN_ALLOW_REMOTE_WRITE` | `false` | Allow write mode (claim hand-off) on non-loopback hosts. Only behind TLS + auth. |
| `IDLESCAN_LOCKDOWN` | `false` | Disable local administrative writes (wallets, airdrops, alerts, backups, scans). |

## Rate Limits and Body Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_MAX_BODY_BYTES` | `1048576` | Maximum request body in bytes. JSON routes require `Content-Type: application/json`. |
| `IDLESCAN_RATE_LIMIT_LOOPBACK` | `false` | Enable rate limiting on loopback too (disabled by default there). |
| `IDLESCAN_RATE_LIMIT_READ_PER_MINUTE` | `240` | Read request capacity per minute per client. |
| `IDLESCAN_RATE_LIMIT_WRITE_PER_MINUTE` | `60` | Write request capacity per minute per client. |
| `IDLESCAN_RATE_LIMIT_SCAN_PER_10_MINUTES` | `12` | Scan request capacity per 10 minutes per client. |

## Outbound Fetch Safety

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_ALLOW_HTTP_FETCH` | `false` | Allow plain HTTP and private addresses for outbound fetches. Off by default; local fixtures only. |
| `IDLESCAN_MAX_FETCH_BYTES` | `26214400` | Maximum response bytes for any outbound fetch (25 MiB). |
| `IDLESCAN_FETCH_TIMEOUT_SECONDS` | `15.0` | Connect/read timeout for outbound fetches. |
| `IDLESCAN_FETCH_ALLOWED_HOSTS` | (none) | Comma-separated exclusive host allowlist for outbound fetches. |

## Multi-Endpoint Verification

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_RPC_QUORUM` | `1` | How many distinct RPC endpoints must agree on critical reads. `2+` = fail closed on disagreement. |
| `IDLESCAN_RPC_<CHAINKEY>` | (none) | Per-chain RPC overrides, comma-separated. Takes precedence over bundled URLs. Example: `IDLESCAN_RPC_ETHEREUM=https://a,https://b` |

## Gas Model and Scan Engine

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_GAS_BUFFER_PCT` | `20` | Safety buffer percent applied to net value after gas. |
| `IDLESCAN_SCAN_WORKERS` | `4` | Bounded thread pool for concurrent chain scanning. |
| `IDLESCAN_SCAN_TIMEOUT_SECONDS` | `300` | Per-scan timeout in seconds. Partial results returned on timeout. |
| `IDLESCAN_PRICE_DISAGREEMENT_PCT` | `10.0` | Flag `price_confidence=low` when providers disagree by more than this percent. |

## Dataset Provenance and Staleness

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_DATASET_STALE_DAYS` | `30` | Warn when a claims dataset file is older than this many days. `0` disables. |

## Process Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_LOG_FORMAT` | `text` | Process log format: `text` or `json`. |
| `IDLESCAN_LOG_LEVEL` | `INFO` | Process log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `IDLESCAN_HTTP_LOG` | `false` | Emit one log line per API request (includes request ID). |

## Alerts and Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_NOTIFY_WEBHOOK_URLS` | (none) | Comma-separated webhook URLs for new alerts. Opt-in, off by default. |
| `IDLESCAN_NOTIFY_MIN_SEVERITY` | `warning` | Minimum severity for notifications: `info`, `warning`, `critical`. |
| `IDLESCAN_NOTIFY_KINDS` | (none) | Comma-separated alert kinds to notify about; empty = all. |
| `IDLESCAN_NOTIFY_DIGEST` | `false` | Send one digest request per scan instead of one per alert. |

## Retention and Backups

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLESCAN_RETENTION_SNAPSHOTS` | `90` | Keep the newest N snapshots. `0` keeps all. |
| `IDLESCAN_RETENTION_ALERTS_DAYS` | `180` | Prune acknowledged/detached alerts older than N days. `0` keeps all. |
| `IDLESCAN_RETENTION_AUDIT_DAYS` | `365` | Prune audit entries older than N days. `0` keeps all. |
| `IDLESCAN_RETENTION_EXECUTIONS_DAYS` | `365` | Prune executions older than N days. `0` keeps all. |
| `IDLESCAN_RETENTION_JOBS_DAYS` | `30` | Prune finished jobs older than N days. `0` keeps all. |
| `IDLESCAN_BACKUP_KEEP` | `5` | Rotate directory backups to the newest N files. |

## Validation

`idlescan doctor --offline` validates configuration and reports unsafe combinations.
`idlescan` refuses to start on a non-loopback host without `IDLESCAN_AUTH_TOKEN` unless
`IDLESCAN_ALLOW_INSECURE_REMOTE=true` is set.
