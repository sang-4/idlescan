# idlescan — Project Analysis

**Analysis date:** 2026-10-26  
**Project version:** 1.1.0 (`pyproject.toml`, `idlescan/__init__.py`)  
**Repository root:** `/home/devx/idlescan`  
**License:** MIT  
**Analysis basis:** full source read, configuration/data review, `idlescan selftest`, and the complete test suite. No source files were modified.

---

## 1. Executive summary

`idlescan` is a **self-hosted, local-first airdrop research desk for EVM wallets**. It does three things in one tool:

1. Finds **idle balances** across 14 EVM chains.
2. Finds **unclaimed airdrop allocations** by combining an eligibility source with a live on-chain claim-state check.
3. Ranks each claim by **net USD value after gas**, verifies merkle proofs against the distributor's on-chain root, and tracks changes between scans.

It ships as a Python package with **zero runtime dependencies**, a SQLite database, a documented JSON API, a CLI, and a hand-written single-page web UI with no build step.

The central design principle is that this is a **research and planning tool, not a signing tool**. The server never holds keys, never signs, and never broadcasts. The default mode is read-only. An optional write mode only unlocks returning an **unsigned transaction** to the user's own browser wallet.

The project is substantially implemented. The current codebase includes the scanner, persistence layer, service layer, API, CLI, 10 frontend views, alerts, snapshots/diffs, exports, calendar feed, backups, demo mode, and an extensive test suite. **148 tests pass**. The main gap is not engineering breadth but **real-world airdrop coverage**: the bundled registry contains only one real entry (Uniswap UNI), and its claims dataset is intentionally not included, so a fresh real scan demonstrates the platform but does not produce a populated live airdrop board without the user supplying data.

---

## 2. What the application is

### 2.1 Positioning

`idlescan` describes itself as "a self-hosted airdrop research desk." It is better understood as:

- a **read-only portfolio and claim scanner** for EVM addresses,
- a **verification layer** for airdrop eligibility data,
- a **local research database** of immutable scan snapshots,
- a **claim planning surface** that compares allocation value against gas cost,
- optionally, a **non-custodial transaction hand-off** to a browser wallet.

It is not an airdrop aggregator, an indexer, a wallet, a signer, a trading bot, or a tax tool.

### 2.2 The problem it addresses

Most "airdrop checkers" ask the distributor contract only whether a given claim index has been claimed. That answers "not claimed" for addresses that were never eligible in the first place. The project explicitly separates two questions:

1. **Was this address owed anything?** This requires an eligibility source: a published merkle dataset, a per-address claim API, or an on-chain allocation view.
2. **Was that allocation already taken?** This is read live from the distributor via `isClaimed(index)` or `claimed(address)`.

Only then can the tool answer the economically relevant question: **is the claim worth the gas?**

Anything that cannot be verified is reported as `unknown`, `no_allocation`, `no_eligibility_source`, or `error` — never silently as "nothing to claim."

### 2.3 Target user

- A single user or researcher tracking their own wallets.
- Someone running a local machine who wants a private, self-hosted alternative to third-party airdrop checkers.
- A power user comfortable with a CLI, `.env` files, RPC endpoints, and merkle-distributor data.
- Not currently aimed at multi-tenant or public SaaS use: there is no authentication or per-user isolation.

---

## 3. What the app does

### 3.1 End-to-end workflow

1. Add one or more EVM addresses to a watchlist (CLI, UI, or bulk import).
2. Configure chains and airdrop registry entries.
3. Run a scan. The scanner reads native and curated ERC-20 balances, checks every enabled airdrop for every tracked wallet, verifies proofs, reads claim state, and estimates gas.
4. The scan is persisted as an **immutable snapshot** with normalized holdings and claim rows.
5. The UI/API presents portfolio totals, actionable claims, deadlines, alerts, history, and diffs versus the previous snapshot.
6. Exports and a calendar feed can be generated.
7. In write mode only, a claim can be prepared as an unsigned transaction and handed to the browser wallet. The server can later record the submitted transaction hash and check its receipt.

### 3.2 Feature inventory

| Area | What is implemented |
| --- | --- |
| **Overview** | Recoverable value after gas, idle balances, deadline watch, alert feed, portfolio pulse, history sparkline, chain mix, status mix, recovery rate, coverage, latest snapshot diff, watchlist |
| **Address check** | Ephemeral read-only check of any address across all enabled drops; nothing is persisted; can add the address to the watchlist afterward |
| **Airdrops** | Registry board with phase filters and sorting; eligibility source, proof status, deadline, dataset status, unclaimed value, safety score |
| **Airdrop profile** | Contract provenance with explorer links, eligibility config, proof counts, status breakdown, value history, tracked positions, executions, alerts, JSON/CSV/Markdown exports |
| **Claims** | Positions ranked by gas-adjusted net value, proof status, status/wallet/below-gas filters, write-mode prepare/confirm flow |
| **Wallets** | Watch-only registry with labels and tags, per-wallet rollups, bulk import up to 200 rows with per-row validation |
| **Wallet profile** | Idle/unclaimed/claimed totals, allocation history, chain mix, positions, balances, executions, alerts |
| **Alerts** | Deadline <14 days, below-gas claims, invalid proofs, new allocations, confirmed claims, missing datasets, missing eligibility sources |
| **Activity** | Immutable snapshots, scan jobs, claim executions, append-only audit log, side-by-side snapshot diffs |
| **Search** | Global command palette (`⌘K` / `Ctrl K` / `/`) over drops, wallets, transaction hashes, and navigation |
| **Calendar** | Standards-compliant `.ics` export with `-P3D` reminders, for one drop or the whole registry |
| **Settings** | Mode, `.env` recipe, chains, exports, SQLite backup, keyboard reference, security model |
| **Exports** | JSON, CSV, Markdown for the latest snapshot and per-drop exports |
| **Maintenance** | Online SQLite backup while the server runs |
| **Demo mode** | Synthetic portfolio and mock chain that populate every screen with no network access |

### 3.3 Status model

The scanner reports these claim states:

| Status | Meaning |
| --- | --- |
| `unclaimed` | Allocation found; contract says it has not been claimed |
| `claimed` | Allocation found and already taken |
| `no_allocation` | Eligibility source has no entry for this address |
| `no_eligibility_source` | Drop is configured but has no dataset/API/view to check |
| `unknown` | Eligibility is known but the claim-state RPC call failed |
| `error` | Proof/root mismatch or another verification problem; do not trust the numbers |

A past deadline is not a separate stored status. It is represented as `expired = true`, and the UI derives an `expired` phase. `claimed` values are hidden from actionable totals.

### 3.4 Supported eligibility shapes

The registry supports three distinct models:

1. **Published merkle dataset**
   - Standard merkle-distributor JSON shapes.
   - Supports `abi.encode`, `abi.encodePacked`, double-hashed leaves, and sorted/unsorted pair hashing.
   - `leaf_encoding: "auto"` tests known encodings against the on-chain root.
   - Proofs are re-derived and verified before an allocation is reported.

2. **Per-address claim API**
   - URL template containing `{address}`.
   - Response is parsed like a dataset.
   - The API's proof is still verified against `merkleRoot()`; a compromised API cannot invent an allocation.

3. **On-chain allocation view**
   - Calls an allocation method such as `allocation(address)`.
   - Uses a separate claimed-state method such as `claimed(address)`.
   - No dataset required.

### 3.5 CLI surface

The CLI has 13 top-level command groups:

| Command | Purpose |
| --- | --- |
| `serve` | Web UI + JSON API; supports `--host`, `--port`, `--db`, `--demo`, `--env-file` |
| `scan` | Scan explicit or saved addresses; supports export, pricing/gas toggles, registry paths, dataset directories, save/track |
| `wallets add/list/rm` | Manage the tracked-wallet registry |
| `claims list` | List claim positions from the latest snapshot, with net-value filters |
| `claims prepare` | Return an unsigned transaction (write mode only) |
| `claims confirm` | Record a submitted transaction hash (write mode only) |
| `alerts` | List alerts, acknowledge one, or acknowledge all |
| `export` | Export the latest snapshot as JSON, CSV, or Markdown |
| `backup` | Write a consistent SQLite copy |
| `calendar` | Emit an iCalendar deadline feed |
| `verify` | Check registry entries against their chains |
| `dataset` | Validate a downloaded claims file against a distributor |
| `chains` / `tokens` | Inspect configured chains and curated tokens |
| `selftest` | Run the full pipeline against a mock chain with zero network |

Exit codes are designed for cron use: `0` success, `1` failed check/command, `2` bad config or arguments.

### 3.6 HTTP API

The API is served by the Python standard library (`ThreadingHTTPServer`) and exposes **34 routes**. All UI functionality is API-backed. Grouped:

| Group | Routes |
| --- | --- |
| Meta | `GET /api/health`, `GET /api/config`, `GET /api/stats` |
| Wallets | `GET/POST /api/wallets`, `POST /api/wallets/bulk`, `GET/PATCH/DELETE /api/wallets/{address}` |
| Airdrops | `GET/POST /api/airdrops`, `GET/PATCH/DELETE /api/airdrops/{id}`, `GET /api/airdrops/{id}/export/{json,csv,md}` |
| Checking | `POST /api/check` (ephemeral, allowed in read-only mode) |
| Scanning | `POST /api/scan`, `GET /api/jobs`, `GET /api/jobs/{id}` |
| Portfolio | `GET /api/portfolio`, `GET /api/snapshots`, `GET /api/snapshots/{id}`, `GET /api/snapshots/{id}/diff` |
| Analytics | `GET /api/analytics`, `GET /api/search` |
| Alerts/audit | `GET /api/alerts`, `POST /api/alerts/{id}/ack`, `POST /api/alerts/ack-all`, `GET /api/audit` |
| Claims | `POST /api/claims/{drop}/{wallet}/prepare`, `POST /api/claims/confirm` (write mode only) |
| Output | `GET /api/export/{json,csv,md}`, `GET /api/calendar.ics`, `POST /api/backup` |

Errors use a consistent mapping: `400` invalid input, `403` read-only refusal, `404` unknown object/route, `409` conflict, `500` unexpected failure. API responses are `no-store`.

### 3.7 Frontend views

The frontend is a hash-routed vanilla JavaScript SPA with 10 views:

| Route | View |
| --- | --- |
| `#/overview` | Portfolio pulse, action queue, alert feed, watchlist, recent snapshots |
| `#/check[/<address>]` | Ephemeral address checker |
| `#/drops` | Airdrop registry board |
| `#/drop/<id>` | Airdrop profile |
| `#/wallet/<address>` | Wallet profile |
| `#/claims` | Claims planner |
| `#/wallets` | Watchlist management and bulk import |
| `#/alerts` | Alert inbox |
| `#/activity` | Snapshots, jobs, executions, audit, diff |
| `#/settings` | Mode, security model, exports, maintenance |

It is responsive from 320 px to 1440 px, includes focus trapping for dialogs/palette, visible focus rings, `aria-current`, reduced-motion support, sticky table columns, and accessible mobile bottom sheets.

---

## 4. Architecture

### 4.1 Layering

```
┌─────────────────────────────────────────────────────────────┐
│  Presentation                                               │
│  CLI (argparse)              Web SPA (HTML/CSS/JS)          │
│  idlescan/cli.py             idlescan/web/*                 │
└───────────────┬─────────────────────────────┬───────────────┘
                │                             │
┌───────────────▼─────────────────────────────▼───────────────┐
│  HTTP API (stdlib ThreadingHTTPServer, router, static files)│
│  idlescan/api.py                                            │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  Application service                                        │
│  Wallets, scans, snapshots, alerts, claim prep, exports,    │
│  analytics, search, calendar, backups, demo seeding        │
│  idlescan/service.py                                        │
└───────────────┬─────────────────────────────┬───────────────┘
                │                             │
┌───────────────▼──────────────┐  ┌───────────▼───────────────┐
│  Scan engine                 │  │  Persistence              │
│  Scanner, Registry, Merkle,  │  │  SQLite schema + queries  │
│  ABI, Keccak, RPC, Prices,   │  │  idlescan/db.py           │
│  Cache, Report               │  │                           │
│  idlescan/scanner.py etc.    │  │                           │
└───────────────┬──────────────┘  └───────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────────┐
│  External read-only services                                │
│  JSON-RPC nodes · DefiLlama · CoinGecko · claim APIs        │
└─────────────────────────────────────────────────────────────┘
```

The **service layer is the single source of business rules**. Both the CLI and the HTTP API call it, so the read-only gate, validation, and persistence behavior cannot diverge between interfaces.

### 4.2 Module map

| Module | Lines | Responsibility |
| --- | ---: | --- |
| `idlescan/scanner.py` | 886 | Core scan engine: balances, token verification, drop checks, proof verification, claim-state reads, gas estimation, pricing attachment |
| `idlescan/service.py` | 1,457 | Application service: wallets, scan jobs, snapshots, alerts, claim prep/confirm, exports, analytics, search, calendar, backups, demo |
| `idlescan/cli.py` | 765 | Full command-line interface |
| `idlescan/db.py` | 606 | SQLite schema and queries; no ORM, idempotent initialization |
| `idlescan/api.py` | 603 | HTTP router, handlers, static server, security headers, ETags |
| `idlescan/report.py` | 349 | Console tables and JSON/CSV/Markdown rendering |
| `idlescan/merkle.py` | 256 | Leaf hashing, proof building/verification, dataset parsing |
| `idlescan/selftest.py` | 231 | Mock chain and offline end-to-end demo |
| `idlescan/abi.py` | 214 | Minimal ABI encoder/decoder and address helpers |
| `idlescan/rpc.py` | 210 | JSON-RPC client with endpoint failover, retries, batching |
| `idlescan/verify.py` | 206 | Registry verification against live chains |
| `idlescan/registry.py` | 169 | Airdrop registry parsing and validation |
| `idlescan/config.py` | 165 | `.env` parsing and settings validation |
| `idlescan/chains.py` | 143 | Chain/token configuration loader |
| `idlescan/prices.py` | 136 | DefiLlama/CoinGecko pricing with graceful degradation |
| `idlescan/keccak.py` | 101 | Dependency-free Keccak-256 |
| `idlescan/cache.py` | 68 | On-disk JSON TTL cache |
| `idlescan/__init__.py` | 48 | Public `scan()` convenience API |
| `idlescan/__main__.py` | 4 | `python -m idlescan` entry point |
| **Python total** | **~6,617** | |
| `idlescan/web/app.js` | 2,771 | Vanilla SPA: routing, views, dialogs, palette, wallet hand-off |
| `idlescan/web/app.css` | 653 | Responsive dark UI |
| `idlescan/web/index.html` | 74 | Shell |
| `idlescan/web/favicon.svg` | 6 | Icon |
| **Frontend total** | **~3,504** | |
| `tests/*.py` | **~2,300** | 148 tests across 11 modules |

### 4.3 End-to-end scan data flow

1. Settings are loaded from environment variables and `.env` (environment wins).
2. The service opens SQLite, initializes the schema, loads chain/token config, and seeds bundled registry entries into the database without clobbering local edits.
3. A scan starts as a background job thread (UI) or synchronously (CLI).
4. For each configured chain:
   - Verify `eth_chainId` against the configured chain ID; skip mismatches with a warning.
   - Read `eth_blockNumber`.
   - Batch `eth_getBalance` and ERC-20 `balanceOf` calls.
   - Verify token contracts with `eth_getCode`, `decimals()`, and `symbol()`; bad tokens are skipped and warned about.
   - For each enabled airdrop on that chain, load eligibility data, verify proofs against `merkleRoot()`, read claim state, build calldata for unclaimed positions, and estimate gas.
5. Prices are fetched in one batched pricing call where possible.
6. The report is filtered by the dust threshold, converted to a JSON payload, and saved as a snapshot plus normalized holdings/claims.
7. Alerts are generated by comparing the new snapshot's claims with the previous snapshot's claims.
8. The UI polls the job and then reads portfolio/analytics endpoints.

### 4.4 Claim hand-off flow (write mode only)

1. `POST /api/claims/{drop}/{wallet}/prepare` (or `idlescan claims prepare`) checks:
   - write mode is enabled,
   - a latest snapshot exists,
   - the position exists and is `unclaimed`,
   - the position is not expired,
   - calldata is present.
2. The service returns an unsigned object: `{chainId, from, to, value, data}` plus claim economics and warnings.
3. The UI optionally uses `window.ethereum` to:
   - request accounts,
   - verify the connected account matches the position wallet,
   - switch to the correct chain,
   - call `eth_sendTransaction`.
4. The transaction is signed and broadcast **by the wallet**, not the server.
5. The user or UI records the hash via `POST /api/claims/confirm`; the service checks the receipt when possible and writes an execution record and audit entry.

This is a deliberate non-custodial design.

### 4.5 Persistence model

SQLite, WAL mode, one file, no ORM. Schema version is stored in `meta` (`SCHEMA_VERSION = 1`).

| Table | Purpose |
| --- | --- |
| `meta` | Schema version |
| `wallets` | Watch-only addresses, labels, tags, archived flag |
| `airdrops` | Registry payloads, enabled flag, source (`registry`, `custom`, `demo`) |
| `jobs` | Scan job lifecycle: queued/running/done/failed, progress, message, result |
| `snapshots` | Immutable scan header: chains, wallets, trigger, totals, full JSON report |
| `holdings` | Normalized balance rows per snapshot |
| `claims` | Normalized claim positions, economics, proof status, calldata |
| `alerts` | Generated signals with severity, wallet/drop links, acknowledged flag |
| `executions` | Claim preparation and submission records |
| `audit` | Append-only action log |

Indexes cover claims by snapshot/wallet, holdings by snapshot, and open alerts. A unique index on `(kind, wallet, drop_id, title)` deduplicates alerts and keeps acknowledged alerts from re-firing; this is deliberate and tested.

The full JSON report is stored alongside normalized rows. That means exports and diffs can reconstruct a snapshot even if downstream normalization evolves.

### 4.6 Notable implementation choices

- **No runtime dependencies.** Keccak-256, ABI encoding/decoding, RPC transport, HTTP server, SQLite access, CSV/ICS generation, and the frontend are all implemented in-house or use the standard library.
- **Custom Keccak-256.** Python's `hashlib.sha3_256` uses different domain separation from Ethereum's Keccak; this implementation avoids incorrect selectors and merkle roots.
- **Custom minimal ABI.** Supports the static and dynamic shapes needed for ERC-20, merkle-distributor, and allocation-view calls, including `bytes32[]`.
- **RPC failover and batching.** Endpoint rotation, retries with jitter, batch requests with automatic fallback to sequential calls, and per-item error capture.
- **Proof-first trust model.** Any third-party eligibility source is only trusted after its proof hashes to the root read directly from the distributor contract.
- **Graceful price degradation.** Missing prices produce `None`/unknown values and raw amounts remain available; they never become zero.
- **Snapshot-centric analytics.** History, recovery rate, coverage, diffs, and sparklines are computed locally from stored snapshots, not from a third party.
- **Deterministic demo mode.** A synthetic ledger populates every screen and is used by both `serve --demo` and `selftest`.

---

## 5. Configuration and bundled data

### 5.1 Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `IDLESCAN_READ_ONLY` | `true` | Enables claim hand-off when `false` |
| `IDLESCAN_HOST` | `127.0.0.1` | Bind address |
| `IDLESCAN_PORT` | `8787` | Bind port |
| `IDLESCAN_DB` | `data/idlescan.db` | SQLite path |
| `IDLESCAN_CHAINS` | empty = all | Chain filter |
| `IDLESCAN_MIN_USD` | `1.0` | Dust threshold |
| `IDLESCAN_REFRESH_MINUTES` | `0` | Background rescan interval |
| `IDLESCAN_ALLOW_REMOTE_WRITE` | `false` | Permit write mode off loopback |
| `IDLESCAN_DATASET_DIR` | unset | Extra dataset directory |
| `IDLESCAN_DEMO` | `false` | Synthetic demo data |

### 5.2 Chains

14 EVM chains are configured in `idlescan/data/chains.json`:

Ethereum, Arbitrum One, OP Mainnet, Base, Polygon PoS, BNB Smart Chain, Gnosis Chain, Avalanche C-Chain, Scroll, Linea, zkSync Era, Celo, Blast, and Mode.

Each entry includes chain ID, native asset metadata, explorer URL, pricing provider identifiers, and multiple public RPC endpoints.

### 5.3 Tokens

58 curated tokens are configured in `idlescan/data/tokens.json` across those chains, primarily stablecoins, wrapped natives, and major governance tokens. Every token is checked on chain (`eth_getCode`, `decimals()`, `symbol()`) before its balance is trusted. This is not an indexer: arbitrary tokens must be added to the configuration.

### 5.4 Airdrop registry

The bundled `idlescan/data/airdrops.json` contains **one real entry**: Uniswap (UNI) on Ethereum, using the classic merkle-distributor shape. Its expected dataset file, `idlescan/data/datasets/uniswap-uni.claims.json`, is **not bundled**; the datasets README explicitly says eligibility data belongs to the project that ran the airdrop and shipping a stale copy would produce wrong answers.

Consequences for a fresh real deployment:

- The registry entry is visible.
- A real scan will report a missing-dataset warning and cannot determine eligibility until the user supplies the file.
- This is intentional and consistent with the "never fake an answer" philosophy.
- The demo mode supplies a synthetic drop and wallets so the full UI can be explored without data.

There is no large built-in directory of live airdrops. Adding drops is a first-class workflow through the UI form, registry JSON, or API.

---

## 6. Security and safety model

### 6.1 What is guaranteed

- The server never stores private keys or seed phrases.
- The server never signs transactions.
- The server never broadcasts transactions.
- The server never requests token approvals or off-chain signatures.
- Claim preparation requires an explicit write-mode switch.
- Write mode refuses to start on a non-loopback host unless `IDLESCAN_ALLOW_REMOTE_WRITE=true`.
- Every state-changing HTTP request requires `X-Idlescan: 1`; a present `Origin` header must match the host.
- Every prepare/confirm action is recorded in the local audit log and executions table.

### 6.2 Important nuance: "read-only" scope

Read-only mode gates **on-chain claim hand-off** (`prepare` and `confirm`). It does **not** freeze the local application. In read-only mode the app can still:

- add/remove wallets,
- add/remove custom airdrops,
- run scans,
- acknowledge alerts,
- write snapshots,
- create backups.

This is reasonable for a local research tool, but it means "read-only" should not be read as "no writes anywhere." A public deployment with no authentication would expose those local administrative operations even while claim preparation remains locked.

### 6.3 HTTP hardening

- Strict Content Security Policy: same-origin scripts and connections, no framing, no objects, no cross-origin forms, no base URI changes.
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, same-origin opener/resource policies, restrictive permissions policy.
- Content-hash ETags for static assets with `304` revalidation.
- HTML revalidates; other assets cache for five minutes.
- Path traversal is rejected by resolving against the web root; unknown SPA paths fall back to `index.html`.
- Query parameters are validated before reaching the service.
- `SIGTERM` performs a clean shutdown.
- Absolute filesystem paths in alert text are scrubbed to filenames on read.

### 6.4 Known security boundaries and risks

| Risk | Assessment |
| --- | --- |
| **No authentication** | Explicitly local-first. Exposing the app requires a reverse proxy with TLS and auth. |
| **No rate limiting** | Acceptable on loopback; a concern if exposed. |
| **Remote claim APIs** | A user-supplied `dataset_url` is fetched by the server. On an exposed/multi-user instance this is an SSRF surface. Proof verification limits false allocations but not network reachability. |
| **Public RPC / price providers** | The tool trusts read-only responses from public endpoints; malicious or broken RPCs could cause wrong reports. Chain-ID checks and proof verification reduce some classes of error. |
| **Malicious registry entry** | A user-created entry can point at the wrong distributor. The UI and docs repeatedly warn to verify the distributor against official project documentation. |
| **Write mode `.env`** | The workspace `.env` currently sets `IDLESCAN_READ_ONLY=false`. This is ignored by git but means a local `serve` here starts in write mode. |
| **Browser wallet hand-off** | The frontend calls `eth_sendTransaction`; the wallet's own confirmation screen is the final security boundary. |

---

## 7. Testing and verification

### 7.1 Test suite

The repository contains **148 tests** in 11 modules:

| Test module | Tests | Focus |
| --- | ---: | --- |
| `test_api.py` | 22 | HTTP routes, headers, ETags, read-only gate, CRUD, exports, profiles, search, calendar, backup, prepare/confirm |
| `test_service.py` | 22 | Demo seeding, alerts, analytics, diffs, search, ephemeral checks, bulk import, backups, claim preparation |
| `test_cli.py` | 22 | Commands, validation, exports, backups, calendar, dataset validation, read-only refusal |
| `test_config.py` | 13 | `.env` parsing, defaults, precedence, remote-write guard, path resolution |
| `test_db.py` | 11 | Schema idempotence, wallets, airdrops, jobs, snapshots, alerts dedupe, executions, audit, stats, history |
| `test_keccak_abi.py` | 11 | Keccak vectors, ABI encoding/decoding, selectors, address validation, EIP-55 |
| `test_merkle.py` | 11 | Uniswap reference root, leaf encodings, proof verification, dataset parsing, amount formats |
| `test_registry.py` | 11 | Bundled registry, defaults, validation errors, disabled entries |
| `test_report.py` | 9 | Console/Markdown output, JSON serialization, exports, totals, calldata |
| `test_scanner.py` | 9 | Happy path, balances, missing dataset, root mismatch, tampered proof, encoding detection, chain mismatch, gas toggle |
| `test_claim_modes.py` | 7 | Merkle API mode, allocation view, expiry, unknown state, tampered API proof, failures |
| **Total** | **148** | |

The suite is offline and does not require network access. Verified during this analysis:

```text
Ran 148 tests in 17.384s
OK
```

### 7.2 Selftest

`python3 -m idlescan selftest` was run successfully. It exercised the full pipeline against a mock chain:

- detected an unclaimed allocation,
- detected an already-claimed allocation,
- verified a merkle proof against a computed root,
- excluded an address with no allocation,
- produced a gas estimate,
- rendered the console report,
- made zero network calls.

### 7.3 Fixtures

`tests/fixtures/uniswap_complex_example.json` comes from Uniswap's merkle-distributor repository (GPL-3.0) and is used only as a test fixture, as documented in the README.

---

## 8. What has been built so far

### 8.1 Complete and working

- **Core scanner**
  - Native and curated ERC-20 balances.
  - Multi-chain RPC with failover and batching.
  - Token contract verification.
  - Merkle dataset loading and proof verification.
  - Remote claim API support.
  - Allocation-view support.
  - Claim state reads.
  - Calldata construction.
  - Gas estimation and gas-adjusted net value.
  - USD pricing through DefiLlama/CoinGecko.
  - Dust filtering and warning surfacing.

- **Local platform**
  - SQLite persistence with snapshots, normalized rows, alerts, executions, and audit.
  - Background scan jobs with progress polling.
  - Immutable snapshots and snapshot diffs.
  - Alert generation and acknowledgement.
  - Wallet registry and bulk import.
  - Airdrop registry with custom entries, enable/disable, and protection of bundled entries.
  - Per-wallet and per-drop profiles.
  - Analytics, recovery rate, coverage, chain/status mix.
  - Search.
  - JSON/CSV/Markdown exports.
  - iCalendar deadline feed.
  - Online SQLite backup.
  - Demo seeding.

- **Interfaces**
  - 13 CLI command groups.
  - 34 HTTP API routes.
  - 10-view SPA with responsive/accessible behavior.
  - Command palette.
  - Optional browser-wallet claim hand-off.

- **Quality**
  - 148 passing tests.
  - Offline selftest.
  - Packaging metadata and console script.
  - README, `.env.example`, examples, dataset instructions.
  - Security headers, CSRF-style header enforcement, remote-write guard.
  - Path sanitization for persisted alert text.

### 8.2 Present but intentionally dependent on user-supplied data

- **Real airdrop coverage.** Only Uniswap UNI is bundled, and its dataset is absent. The platform is built; the live registry is not populated.
- **Token coverage.** 58 curated tokens, not arbitrary token discovery.
- **Remote claim APIs.** Supported, but no real API endpoints are bundled.
- **Allocation-view drops.** Supported, but no real example is bundled.

### 8.3 Not present

- User accounts, authentication, authorization, or multi-tenancy.
- Dockerfile, Compose file, systemd unit, Kubernetes manifests, or CI workflow.
- Database migration framework beyond schema versioning and idempotent initialization.
- Non-EVM chains (Solana, Cosmos, etc.).
- NFT or LP-position discovery.
- Historical price or cost-basis tracking.
- Tax reporting.
- A hosted/public service mode with hardened auth and rate limiting.
- An automated airdrop data pipeline or indexer.
- Git history in this workspace: the directory is **not a git repository**, so a commit timeline or changelog could not be derived.

### 8.4 Observed local workspace state

The working directory contains runtime state in addition to source:

- `data/idlescan.db` — 114,688 bytes.
- Demo data in that database: 3 demo wallets, 2 airdrops (the bundled Uniswap entry plus `demo-merkle`), 5 snapshots, 20 holdings, 15 claim rows, 2 alerts, 3 executions, and 8 audit entries.
- `.env` exists and currently sets `IDLESCAN_READ_ONLY=false`; `.env.example` keeps `true`.
- The `.gitignore` excludes `.env`, `data/`, `*.db`, and dataset JSON files, so these are runtime/local artifacts rather than intended source deliverables.
- `__pycache__` directories and `.pyc` files are present from earlier runs.

This local state strongly suggests the app has been run in demo mode and exercised through the UI, including multiple scans.

---

## 9. Strengths

1. **Clear and defensible threat model.** The separation of eligibility, claim state, proof verification, and gas economics is the right abstraction for this problem.
2. **Strong correctness posture.** Proofs are verified against the on-chain root; missing data is surfaced as a warning/status, not coerced to zero.
3. **Excellent operability for a local tool.** Zero dependencies, one SQLite file, one command to serve, one command to selftest.
4. **Interfaces are coherent.** The CLI, API, and UI share the same service layer, so behavior is consistent.
5. **Security-conscious default.** Read-only by default, loopback bind, no keys, no signing, no broadcasting, explicit remote-write opt-in.
6. **Broad platform surface for a small codebase.** Snapshots, diffs, alerts, analytics, profiles, exports, calendar, backups, search, audit, and demo mode are all implemented.
7. **Test depth.** 148 offline tests cover the engine, persistence, API, CLI, security, and claim modes.
8. **No frontend build step.** The SPA is inspectable and deployable as static files.

---

## 10. Weaknesses, gaps, and recommendations

### 10.1 Product and data gaps

| Gap | Impact | Recommendation |
| --- | --- | --- |
| Only one real bundled airdrop, with no dataset | A new user sees a platform but no populated real opportunities | Provide a curated, versioned set of public datasets with provenance and verification timestamps; make dataset acquisition a documented workflow |
| No automated data pipeline | Registry upkeep is manual | Add an optional importer/validator job for published distribution files and claim APIs |
| EVM-only | Out of scope for Solana/Cosmos users | Keep explicit; consider a plugin-style chain adapter if expanded |
| Curated token list | Idle balances missing for unlisted tokens | Add token discovery via logs/indexer or per-wallet token lists |
| No NFT/LP positions | Incomplete portfolio view | Document as out of scope or add adapters |

### 10.2 Engineering and operational gaps

| Gap | Impact | Recommendation |
| --- | --- | --- |
| No authentication or rate limiting | Public exposure is unsafe | Keep local-first; if exposing, add reverse-proxy auth and rate limiting, or build auth |
| No deployment artifacts/CI | Manual setup and no automated regression gate | Add Dockerfile/Compose, systemd example, and a CI workflow running the 148 tests |
| Limited schema migration story | Future schema changes could be awkward | Formalize migrations keyed by `schema_version` |
| Single-file 2,771-line frontend | Maintainability burden | Consider module splitting without adding a build requirement (ES modules) |
| EIP-1559 gas model not explicit | `eth_estimateGas × eth_gasPrice` may misestimate on modern chains | Add base-fee/priority-fee awareness or label the estimate clearly |
| Public RPC/price dependence | Rate limits or provider outages degrade scans | Document provider configuration; support authenticated RPC URLs; surface price errors prominently |
| Alert dedupe is global by title/kind | Some alerts will not re-fire after acknowledgement | Confirm this is desired; consider snapshot-scoped dedupe for recurring signals |
| Remote `dataset_url` fetch | SSRF surface on exposed instances | Restrict schemes/hosts or make remote fetching opt-in for shared deployments |
| `.env` currently enables write mode | Local operator may not realize | Keep `.env` out of version control; consider a startup banner already present in UI |
| No structured logs/metrics | Troubleshooting is manual | Add opt-in structured logging; HTTP logging is currently behind `IDLESCAN_HTTP_LOG=1` |

### 10.3 Documentation drift

- `idlescan/keccak.py` refers to `tests/test_keccak.py`; the actual test file is `tests/test_keccak_abi.py`.
- README language such as "the only state-changing feature" is accurate for on-chain actions but can be misread as "the app writes nothing else." The local administrative writes are separate.

---

## 11. Maturity assessment

| Dimension | Assessment |
| --- | --- |
| **Core functionality** | Strong. Scanner, proof verification, pricing, gas economics, persistence, and claims workflow are implemented and tested. |
| **UX** | Strong for a self-hosted tool: 10 views, command palette, responsive layout, accessible dialogs, demo mode. |
| **Data coverage** | Early. One real registry entry and no bundled dataset; the platform is more complete than the catalog. |
| **Security** | Good for local single-user use. Not ready for public multi-user exposure without auth, rate limiting, and SSRF review. |
| **Operability** | Good local story: zero deps, SQLite, CLI, backup, calendar, exports, demo/selftest. Missing deployment/CI artifacts. |
| **Test maturity** | Strong relative to project size: 148 offline tests, including security and claim-mode behavior. |
| **Overall** | A well-built, unusually complete **local-first v1.1 research desk**. It is ready for personal/local use today. It is not yet a turnkey public airdrop service or a populated airdrop database. |

---

## 12. Quick start

```bash
cd /home/devx/idlescan

# Offline end-to-end demo, no network
python3 -m idlescan selftest

# Demo UI at http://127.0.0.1:8787
IDLESCAN_DEMO=true python3 -m idlescan serve

# Real usage
python3 -m idlescan wallets add 0xYourAddress --label "main"
python3 -m idlescan scan --saved --save --track
python3 -m idlescan serve
```

Install as a console script if desired:

```bash
pip install -e .
idlescan --help
```

Run the tests:

```bash
python3 -m unittest discover -s tests
```

For real airdrop checking, add a registry entry and provide the matching claims dataset. Validate the dataset against the distributor before trusting it:

```bash
idlescan dataset path/to/claims.json \
  --chain ethereum \
  --distributor 0xDistributorAddress
```

---

## 13. Bottom line

`idlescan` is a **complete, thoughtfully engineered local airdrop research platform**, not a prototype. Its strongest ideas are the separation of eligibility from claim state, mandatory proof verification against on-chain roots, gas-adjusted ranking, immutable snapshots, and a strict no-keys/no-signing posture.

What has been built so far is the **platform**: scanner, storage, service layer, API, CLI, SPA, alerts, analytics, exports, calendar, backups, demo mode, and tests. What is still missing is **breadth of live airdrop data** and the operational hardening needed for public or multi-user deployment. For a single user running it locally, it is usable and coherent today. For a public service, it remains a strong foundation that needs authentication, rate limiting, deployment automation, and a maintained data pipeline.
