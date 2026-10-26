# idlescan Architecture

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  CLI (cli.py)              SPA (web/*.html, *.js, *.css)   │
└────────────────┬──────────────────────────────┬────────────┘
                 │                              │
┌────────────────▼──────────────┐  ┌────────────▼─────────────┐
│  HTTP API (api.py)            │  │  Static assets (CSP)     │
│  ThreadingHTTPServer          │  │  Same-origin, no inline  │
└────────────────┬──────────────┘  └──────────────────────────┘
                 │
┌────────────────▼──────────────────────────────────────────┐
│  PlatformService (service.py)                              │
│  Single source of business rules                            │
│  • Wallets, airdrops, scans, alerts                         │
│  • Custom tokens, registry, backups                         │
│  • Read-only gate, lockdown, auth                           │
└────────────────┬──────────────────────────────────────────┘
                 │
        ┌────────┴────────┬─────────┬──────────┬─────────┐
        ▼                 ▼         ▼          ▼         ▼
  scanner.py       rpc.py      merkle.py   prices.py   db.py
  Scan engine      RPC client   Merkle      Price       SQLite
  (concurrent      Quorum/health proofs      Oracle      WAL, migrations,
   chains, cancel)            verification                        retention
```

## Module Map

| Module | Responsibility |
|--------|---------------|
| `abi.py` | ABI encoding/decoding for claim calldata |
| `api.py` | HTTP API server, routes, security headers, metrics |
| `cache.py` | On-disk response cache |
| `chains.py` | Chain configuration (RPCs, native assets, fee models) |
| `config.py` | Environment settings, validation |
| `dataset.py` | Streaming claim parser, provenance, staleness |
| `db.py` | SQLite persistence, migrations, retention |
| `doctor.py` | `idlescan doctor` deployment checks |
| `gas.py` | EIP-1559/legacy fee estimation, L1 fees, buffers |
| `keccak.py` | Keccak-256 hash |
| `log.py` | Redacting structured process logs |
| `merkle.py` | Merkle tree proof construction/verification |
| `migrations.py` | Ordered schema migrations |
| `notifications.py` | Fire-and-forget webhook notifier |
| `preflight.py` | Claim preflight checks |
| `prices.py` | Price oracle |
| `redaction.py` | Secret redaction for URLs/tokens |
| `registry.py` | Airdrop registry parsing |
| `report.py` | Scan report serialization (JSON/CSV/MD) |
| `rpc.py` | RPC client with quorum, health, retries |
| `safe_http.py` | SSRF-safe outbound HTTP |
| `scanner.py` | Scan engine (concurrent chains, cancel, timeout) |
| `selftest.py` | Offline end-to-end demo |
| `service.py` | Platform service (business rules) |
| `verify.py` | Registry verification against chains |

## Data Flow

### Scan

1. CLI or API calls `PlatformService.start_scan()`
2. Service creates a job, starts a background thread
3. Thread builds a `Scanner` (concurrent chains, `scan_workers`)
4. Scanner iterates chains in parallel via `ThreadPoolExecutor`:
   - Reads block number via quorum RPC
   - Scans native balances and token balances via batch RPC calls
   - For each enabled drop on the chain: checks claim state, verifies merkle proofs
5. Prices are fetched via the price oracle
6. Report is serialized to dict and saved as a snapshot
7. Alerts are generated (condition scopes, escalation tiers)
8. Notifications are dispatched (fire-and-forget, bounded retries)
9. Job is marked done/failed/cancelled

### Claim Preparation

1. User requests `POST /api/claims/{drop}/{wallet}/prepare`
2. Service performs preflight: re-reads claim state, root, distributor code, simulated call
3. Calldata integrity is verified (selector, arguments, proof)
4. Unsigned claim transaction is returned (never signed or broadcast)

## Database Schema

SQLite (WAL mode), one file. Schema version 2.

| Table | Purpose |
|-------|---------|
| `meta` | Schema version |
| `wallets` | Tracked addresses |
| `airdrops` | Registry entries |
| `snapshots` | Scan results (immutable) |
| `holdings` | Token balances per snapshot |
| `claims` | Claim positions with gas/net metadata |
| `alerts` | Condition and event alerts (deduped) |
| `executions` | Claim preparation records |
| `audit` | Append-only administrative log |
| `jobs` | Scan job state |
| `notifications` | Webhook delivery attempts |
| `custom_tokens` | Operator-added token overrides |
| `dataset_provenance` | Per-drop dataset metadata |

## Security Boundaries

- **RPC endpoints**: operator-configured, not from API input; quorum-verified
- **Outbound HTTP**: SSRF-safe client (`safe_http.py`); HTTPS-only by default
- **API input**: validated at boundary and service layer; bearer auth when configured
- **Secrets**: redacted in logs, errors, audit, API responses

## Concurrency Model

- Scan runs in a background thread; single-flight lock prevents concurrent scans
- Chains scanned in parallel via `ThreadPoolExecutor` (bounded by `scan_workers`)
- Cancellation via `threading.Event`, checked between chains and drops
- SQLite connections are per-operation (no shared connections across threads)
