# idlescan

**A self-hosted airdrop research desk.** It finds idle balances and unclaimed
allocations across 14 EVM chains, prices every claim against its gas cost, verifies
merkle proofs against the on-chain root, and tracks what changed between scans — in a
web UI and from the CLI. You can also check any address without adding it to your
watchlist.

Read-only by default. One `.env` switch unlocks the only state-changing feature: handing
an **unsigned** claim transaction to your own browser wallet. The server never holds
keys, never signs, and never broadcasts.

```bash
cd idlescan
pip install -e .          # or: python3 -m idlescan ...
IDLESCAN_DEMO=true idlescan serve
# http://127.0.0.1:8787
```

![status](https://img.shields.io/badge/mode-read--only-5eead4) ![python](https://img.shields.io/badge/python-3.10%2B-c2f750) ![deps](https://img.shields.io/badge/runtime%20deps-zero-8b99ad)

## Why another airdrop checker

Most "unclaimed airdrop" tools only ask the distributor *"was index N claimed?"* — which
answers "not claimed" for every address on earth. `idlescan` separates the two questions:

1. **Was this address owed anything?** A published merkle dataset, a claim API, or an
   on-chain allocation view. Kept in a reviewable registry.
2. **Was that allocation already taken?** `isClaimed(index)` / `claimed(address)` on the
   distributor, read live.

Then it answers the question that actually matters: **is the claim worth the gas?**
Allocations are ranked by net USD after `eth_estimateGas` × `eth_gasPrice`, not by
headline token value. Anything it cannot verify is shown as `unknown` or
`no eligibility source` — never as "nothing to claim".

## What you get

| | |
| --- | --- |
| **Overview** | Recoverable total net of gas, idle balances, deadline watch, alert feed, portfolio pulse (history sparkline, chain and status mix, recovery rate, coverage), latest snapshot diff, safety watchlist |
| **Check** | `#/check` or `#/check/<address>` — read-only eligibility check for any address across every enabled drop, with per-position value, gas, net, deadline and proof status. Nothing is persisted, so it doubles as a public checker when the instance is exposed |
| **Airdrops** | Registry board with phase filters, sorting, verifiable safety score (eligibility source, proof verification, links, deadline), dataset status, per-drop unclaimed value |
| **Airdrop profile** | `#/drop/<id>` — contract provenance with explorer links, eligibility source, proof counts, status breakdown, value history, tracked positions, executions and alerts, per-drop JSON/CSV/Markdown exports |
| **Claims** | Profitability-ranked positions, gas-adjusted net, proof status, one-click **unsigned** transaction hand-off (write mode) |
| **Wallets** | Watch-only addresses with labels and tags, per-wallet rollups, and bulk import of up to 200 addresses (address or `address, label, tags` per line) with per-row validation |
| **Wallet profile** | `#/wallet/<address>` — idle/unclaimed/claimed totals, allocation history, chain mix, full position and balance tables, linked executions and alerts |
| **Alerts** | Deadline <14 days, below-gas claims, invalid proofs, new allocations, confirmed claims, missing datasets |
| **Activity** | Immutable snapshots, scan jobs, claim executions, append-only audit log, side-by-side snapshot diffs |
| **Command palette** | `⌘K` / `Ctrl K` or `/` — global search across drops, wallets, transactions and pages |
| **Calendar** | Download documented deadlines as iCalendar (`.ics`) with three-day reminders, per drop or for the whole registry |
| **Settings** | Mode, `.env` recipe, chains, exports, one-click SQLite backup, keyboard reference, security model — with no way to flip the mode from the UI |

The scanner is unchanged in spirit: native + curated ERC-20 balances, USD pricing from
DefiLlama, claim-state reads, gas estimates, JSON/CSV/Markdown exports. Every proof is
re-derived and checked against `merkleRoot()` before it is reported.

## Explorer views and shortcuts

The frontend is a hash-routed single-page app, so every view is a shareable URL:

| route | view |
| --- | --- |
| `#/overview` | portfolio pulse, action queue, analytics, diff summary, deadlines, recent snapshots |
| `#/check[/<address>]?address=<address>` | address eligibility checker (no watchlist entry required) |
| `#/drops` | registry board, phase filter, sort by net/deadline/safety/name |
| `#/drop/<id>` | airdrop profile: provenance, eligibility, proofs, positions, history, exports |
| `#/wallet/<address>` | wallet profile: totals, allocation history, positions, balances, activity |
| `#/claims` | claims planner with status, wallet and below-gas filters |
| `#/wallets` | tracked addresses |
| `#/alerts` | open and acknowledged alerts |
| `#/activity` | snapshots, jobs, executions, audit, snapshot diff |
| `#/settings` | mode, security model, exports, maintenance |

**Command palette.** Press `⌘K` (`Ctrl K` on Windows/Linux) or `/` outside a text field
to search airdrops, wallets, transaction hashes and navigation. Arrow keys move, `Enter`
opens, `Esc` closes. Search is served by `GET /api/search` and requires at least two
characters.

**Portfolio pulse.** The overview combines every stored snapshot into a combined-value
sparkline, idle-by-chain bars, claim-status mix, recovery rate, wallet coverage, a
deadline radar and a summary of the latest snapshot diff. Nothing is fetched from a
third party; trends are computed from your snapshot history.

**Address checker.** `#/check` answers "is this address owed anything?" without touching
the watchlist. It validates the address, runs one read-only pass over the enabled drops
(or the synthetic ledger in demo mode), and returns idle balances, unclaimed and claimed
allocations, gas-adjusted net value, deadlines, proof status and any scan warnings. The
result can be added to the watchlist with one click afterwards. Nothing about the check
is written to the database: no wallet row, no snapshot, no alert. `POST /api/check` is
allowed in read-only mode for the same reason, and `idlescan check <address>` gives the
same result in the terminal (`--json` for scripting).

**Bulk import.** Wallets → **Import list** accepts up to 200 rows, one per line:

```
0xabc…                     # address only
0xdef…, Cold wallet        # address + label
0x123…, Research, defi     # address + label + tags
```

Valid rows are imported even when others fail; the modal reports every skipped row
(invalid address or duplicate). Duplicate addresses already on the watchlist are updated
rather than inserted twice. The action is recorded as `wallet.bulk` in the audit log.

**Freshness.** The footer shows the application version and how long ago the displayed
snapshot was captured. If the snapshot is older than roughly twice the configured
`IDLESCAN_REFRESH_MINUTES` budget (or more than a day when auto-refresh is off), a stale
snapshot banner asks you to rescan before acting on the numbers.

**Responsiveness and accessibility.** The desk is tested from 320 px to 1440 px: stats
reflow, tables scroll horizontally with a sticky first column and an edge fade, long
addresses and paths wrap instead of forcing page overflow, wide modals become bottom
sheets on phones, and the nav moves to its own full-width row on tablets and scrolls the
active section into view on phones. Dialog and palette focus is trapped and restored to
the trigger on close; `aria-current` marks the active section; controls have visible
focus rings; `prefers-reduced-motion` disables animation.

**Snapshot diffs.** Every scan is immutable. Use **Latest diff** on the overview or
**Diff** beside any snapshot in Activity to see new allocations, claims, expirations,
proof changes, status changes, value moves and removals, with idle/unclaimed/combined
deltas.

**Deadline calendar.** `GET /api/calendar.ics` (or the buttons on a drop profile and in
Settings) returns a standards-compliant iCalendar feed. Each event carries the chain,
distributor, tracked unclaimed value, notes, a link and a `-P3D` display alarm. Subscribe
to it from any calendar app, or download a single drop with `?drop=<id>`.

**Backups.** Settings → Maintenance (or `POST /api/backup`, or `idlescan backup`) writes
a consistent SQLite copy through SQLite's online backup API while the server keeps
running. The default destination is `data/backups/idlescan-<timestamp>.db`. Backups are
deliberate and local; nothing is uploaded.

**Keyboard.** In addition to the palette, `Esc` closes any modal and every destructive
action asks for confirmation. The UI never flips read-only mode.

## Install and run

Python 3.10+. Zero runtime dependencies — the web server is stdlib `http.server`, the
database is SQLite, and the frontend is hand-written HTML/CSS/JS with no build step.
Keccak-256 and ABI encoding are implemented in the package.

```bash
git clone <repo> && cd idlescan
python3 -m idlescan selftest          # offline end-to-end demo, zero network
python3 -m idlescan serve             # web UI + JSON API on 127.0.0.1:8787
python3 -m idlescan serve --demo      # synthetic data, every screen populated
```

Or install the console script:

```bash
pip install -e .
idlescan --help
```

First real run:

```bash
idlescan wallets add 0xd8dA...6045 --label "main"
idlescan scan --saved --save --track   # scan tracked wallets, persist a snapshot
idlescan serve
```

## The read-only switch

Read-only is the default and the safe mode. The scanner, dashboard, exports and alerts
work identically without it.

```dotenv
# .env — copy .env.example and edit
IDLESCAN_READ_ONLY=false
```

What that unlocks, exactly:

- `POST /api/claims/{drop}/{wallet}/prepare` and `idlescan claims prepare` return an
  **unsigned transaction object** (`to`, `data`, `value`, `chainId`).
- `POST /api/claims/confirm` records a transaction hash you already sent, checks the
  receipt when possible, and writes an audit entry.

What it does **not** do: store private keys or seed phrases, sign anything, broadcast
anything, ask for token approvals, or request off-chain signatures. Your browser wallet
(`window.ethereum`) or your own tooling signs and sends.

Extra guards:

- Write mode refuses to start on a non-loopback host unless
  `IDLESCAN_ALLOW_REMOTE_WRITE=true` is also set.
- Every state-changing request needs the `X-Idlescan: 1` header; an `Origin` header, if
  present, must match the server. Random web pages cannot POST to a locally served
  instance.
- Prepare only works for an unclaimed, unexpired position with calldata in the latest
  snapshot, and the connected wallet account must match the position wallet before the
  UI will send.
- Every prepare/confirm is appended to the local audit log.

The UI deliberately cannot change the mode. Edit `.env` and restart.

## CLI

| command | what it does |
| --- | --- |
| `serve` | web UI + JSON API (`--host`, `--port`, `--db`, `--demo`, `--env-file`) |
| `wallets add/list/rm` | manage the tracked-wallet registry |
| `scan` | balances + claim status + USD/gas/net; `--save` persists a snapshot |
| `check` | eligibility check for one address without tracking it (`--json`) |
| `claims list` | claim positions from the latest snapshot |
| `claims prepare` | unsigned claim transaction (**requires write mode**) |
| `claims confirm` | record a submitted transaction hash (**requires write mode**) |
| `alerts` | open alerts, `--ack ID`, `--ack-all` |
| `export` | latest snapshot as `--format json\|csv\|md` |
| `backup` | consistent SQLite copy (`--out FILE_OR_DIR`, `--json`) |
| `calendar` | iCalendar deadline feed (`--drop ID`, `--out FILE`) |
| `verify` | check registry entries against their chains |
| `dataset` | validate a downloaded claims file against a distributor |
| `chains` / `tokens` | list configured chains and curated tokens |
| `selftest` | full pipeline against a mock chain, no network |

Examples:

```bash
# scan tracked wallets and keep the snapshot for the UI
idlescan scan --saved --save --track

# scan explicit addresses without touching the database
idlescan scan 0xabc... 0xdef... --chains ethereum,base,arbitrum \
  --out reports/october --format json,csv,md

# inspect the latest positions
idlescan claims list --min-net 25
idlescan claims list --json

# check an address without adding it to the watchlist
idlescan check 0xabc... --json

# write-mode hand-off from the terminal
idlescan claims prepare mega-drop 0xabc...
idlescan claims confirm mega-drop 0xabc... 0x<tx-hash>

# alerts and exports from cron
idlescan alerts
idlescan export --format md --out reports/latest.md

# maintenance
idlescan backup --out data/backups/
idlescan calendar --out deadlines.ics
idlescan calendar --drop uniswap-uni          # one drop, printed to stdout
```

`scan` flags include `--chains`, `--min-usd`, `--no-prices`, `--no-gas`, `--all`,
`--json`, `--registry`, `--dataset-dir`, `--save`, `--saved`, `--track`, `--env-file`.

Exit codes: `0` success, `1` a check/command failed (including read-only refusal), `2`
bad config or arguments. Safe for cron.

## Configuration

All settings are optional except the toggle. Environment variables win over `.env`.

| variable | default | meaning |
| --- | --- | --- |
| `IDLESCAN_READ_ONLY` | `true` | `false` unlocks unsigned claim hand-off |
| `IDLESCAN_HOST` | `127.0.0.1` | bind address |
| `IDLESCAN_PORT` | `8787` | bind port |
| `IDLESCAN_DB` | `data/idlescan.db` | SQLite database (relative to the install directory) |
| `IDLESCAN_CHAINS` | empty = all | comma-separated chain keys |
| `IDLESCAN_MIN_USD` | `1.0` | dust threshold |
| `IDLESCAN_REFRESH_MINUTES` | `0` | background rescan interval (0 = manual) |
| `IDLESCAN_ALLOW_REMOTE_WRITE` | `false` | permit write mode on a non-loopback host |
| `IDLESCAN_DATASET_DIR` | — | extra directory for claim datasets |
| `IDLESCAN_DEMO` | `false` | seed/use synthetic demo data |

Chain and token metadata live in `idlescan/data/chains.json` and
`idlescan/data/tokens.json`. Replace `rpcs` with your own provider for speed; every
token is checked on chain (`eth_getCode`, `decimals()`) before its balance is trusted.

## Adding an airdrop

Use the **Airdrops → Add airdrop** form, or edit `idlescan/data/airdrops.json` and run
`idlescan verify`. There is no universal eligibility API, so each entry declares both
halves. Three eligibility shapes are supported.

**1. Published merkle dataset** (most common):

```json
{
  "id": "project-token",
  "name": "Project (TOKEN)",
  "chain": "arbitrum",
  "distributor": "0xDistributorContract",
  "token": "0xTokenAddress",
  "token_decimals": 18,
  "eligibility": {
    "type": "merkle_dataset",
    "dataset": "datasets/project-token.claims.json",
    "leaf_encoding": "auto"
  },
  "claim_method": "claim(uint256,address,uint256,bytes32[])",
  "claimed_method": "isClaimed(uint256)",
  "root_method": "merkleRoot()",
  "claim_deadline": "2026-12-31T23:59:59Z",
  "links": { "claim": "https://project.example/claim" }
}
```

`leaf_encoding: "auto"` tries `abi.encodePacked` and `abi.encode` leaves (plus
double-hashed variants) against the on-chain root, so you do not have to guess which fork
of merkle-distributor a project used.

**2. Per-address claim API**:

```json
"eligibility": { "type": "dataset_url", "url": "https://project.example/api/claims/{address}" }
```

The response is parsed like a dataset and its proof is still checked against
`merkleRoot()`. A compromised API cannot invent an allocation.

**3. On-chain allocation view**:

```json
"eligibility": {
  "type": "allocation_view",
  "method": "allocation(address)",
  "returns": "uint256",
  "decimals": 18
},
"claimed_method": "claimed(address)"
```

Every entry can carry `claim_deadline`, `notes`, and `links`. A past deadline marks
unclaimed rows as `expired`, not claimable.

### Statuses

| status | meaning |
| --- | --- |
| `unclaimed` | allocation found; contract says it has not been claimed |
| `claimed` | allocation found, already taken |
| `no_allocation` | eligibility source has no entry for this address |
| `no_eligibility_source` | drop configured, but no dataset/API/view to check eligibility |
| `unknown` | eligibility known, but the claim-state call failed |
| `error` | proof/root mismatch — investigate, do not trust the numbers |

### Datasets

The usual merkle-distributor JSON is accepted, plus two convenience shapes:

```json
{ "merkleRoot": "0x…", "claims": { "0xabc…": { "index": 12, "amount": "0x1bc16d674ec80000", "proof": ["0x…"] } } }
```

```json
{ "claims": [ { "account": "0xabc…", "index": 12, "amount": "300000000000000000000", "proof": [] } ] }
```

Amounts may be decimal, hex, or scientific notation. Put files in
`idlescan/data/datasets/`, or point `--dataset-dir` at a directory. Validate before
trusting:

```bash
idlescan dataset claims.json --chain arbitrum --distributor 0x…
idlescan dataset claims.json --root 0x… --chain ethereum --distributor 0x…
```

## HTTP API

Everything the UI does is a documented JSON endpoint, so you can script it. State-changing
calls need `X-Idlescan: 1`.

```
GET    /api/health                 GET    /api/config          GET  /api/stats
GET    /api/wallets                POST   /api/wallets
POST   /api/wallets/bulk                                  # up to 200 rows, per-row errors
GET    /api/wallets/{address}                              # wallet profile
PATCH  /api/wallets/{address}      DELETE /api/wallets/{address}
POST   /api/check                                         # ephemeral eligibility check
GET    /api/airdrops               POST   /api/airdrops
GET    /api/airdrops/{id}                                  # airdrop profile
GET    /api/airdrops/{id}/export/{json|csv|md}             # per-drop export
PATCH  /api/airdrops/{id}          DELETE /api/airdrops/{id}
GET    /api/search?q=…&limit=…                             # drops, wallets, tx hashes
GET    /api/analytics                                     # history, breakdowns, latest diff
POST   /api/scan                   GET    /api/jobs            GET  /api/jobs/{id}
GET    /api/portfolio?snapshot=…   GET    /api/snapshots       GET  /api/snapshots/{id}
GET    /api/snapshots/{id}/diff                           # diff against previous snapshot
GET    /api/alerts                 POST   /api/alerts/{id}/ack POST /api/alerts/ack-all
GET    /api/audit
POST   /api/claims/{drop}/{wallet}/prepare        # write mode only
POST   /api/claims/confirm                        # write mode only
GET    /api/export/{json|csv|md}
GET    /api/calendar.ics[?drop={id}]                       # iCalendar deadline feed
POST   /api/backup                                        # local SQLite backup
```

Error handling is consistent: `400` invalid input (including non-integer query parameters
and `limit > 25`), `403` read-only refusal, `404` unknown route or object, `409` conflict
(e.g. preparing an already-claimed position), `500` unexpected failure. All responses are
`no-store` JSON with the security headers described below.

### HTTP hardening

- A strict Content-Security-Policy allows only same-origin scripts, same-origin
  connections, inline styles (the UI uses inline CSS variables) and no framing,
  objects, forms to other origins or base-uri changes.
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, same-origin opener/resource policies and a restrictive
  permissions policy are sent on every response, including downloads.
- Static assets carry a content-hash `ETag`; `If-None-Match` returns `304` with no body.
  HTML revalidates (`no-cache`), other assets are cached for five minutes.
- Query parameters are validated before reaching the service: snapshot ids and search
  limits must be integers and search limits are capped.
- `SIGTERM` triggers a clean shutdown (useful for systemd/Docker); `Ctrl-C` still works.
- Path traversal is rejected by resolving static paths against the web root; unknown SPA
  paths fall back to `index.html` but never outside it.


## Safety

This tool finds positions; claiming is a separate, risky action.

- Verify the **distributor address** against the project's official docs before signing.
  A malicious registry entry or dataset can otherwise point you at the wrong contract.
- A legitimate claim needs **no token approval** and no signature beyond the transaction
  itself. If a site asks you to approve, sign a message, or "connect to verify
  eligibility", stop.
- Every DM and "airdrop checker" site is a drainer until proven otherwise. The attack is
  a `Permit`/`approve` signature or an `eth_sign` blob, not a claim.
- Use a wallet that holds only what you are claiming; revoke approvals afterwards
  (revoke.cash) if you already interacted.
- `idlescan` never needs your keys. Do not give them to any tool that asks.

## Deployment

Local-first: the default bind is loopback and the database stays on disk. The only
outbound traffic is read-only RPC calls plus DefiLlama price lookups.

If you must expose it:

1. Keep `IDLESCAN_READ_ONLY=true` unless you have a specific reason not to.
2. Put a reverse proxy with TLS and authentication in front; there is no built-in auth.
3. If write mode is truly required remotely, set `IDLESCAN_ALLOW_REMOTE_WRITE=true` and
   understand that the remote client still signs in its own wallet. Prefer a VPN.
4. Back up `data/idlescan.db` like any other research record — or let
   `idlescan backup` / Settings → Maintenance write a consistent copy to
   `data/backups/` while the server runs.

## Development

```bash
python3 -m unittest discover -s tests     # 149 tests, no network required
```

The Keccak-256 implementation is checked against canonical vectors, the merkle code
against Uniswap's own `complex_example.json` fixture (MIT) with a root produced by ethers
v5, and the scanner against a mock chain covering claimed/unclaimed, missing
allocations, root mismatches, tampered proofs, and encoding auto-detection. The platform
adds tests for the `.env` contract, SQLite persistence, history rollups, the write gate
(service + HTTP), CSRF header enforcement, static serving with ETag revalidation,
security headers, validated query parameters, explorer profiles, analytics, snapshot
diffs, search, calendar output, per-drop exports, backups, the ephemeral address checker,
bulk wallet import, path sanitisation in alerts, and demo seeding.

## Limitations

- EVM chains only. Solana/Cosmos airdrops are out of scope.
- An address missing from a dataset may still have been eligible in another snapshot; the
  tool reports what the data says.
- Eligibility is only as good as the dataset/API you feed it. Proof checks protect against
  corrupted or malicious claim data, not against an incomplete published list.
- Gas estimates can revert (e.g. state changed mid-scan); a missing gas value means
  "could not estimate", not "free".
- Token discovery uses a curated list, not an indexer. Extend `tokens.json`.
- Nothing here is investment advice.

## License

MIT. `tests/fixtures/uniswap_complex_example.json` is from
[Uniswap/merkle-distributor](https://github.com/Uniswap/merkle-distributor) (GPL-3.0),
used only as a test fixture.
# idlescan
