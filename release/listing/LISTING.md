# idlescan Desk — Gumroad listing copy

Seller: Allan Kiprop (individual) · https://allank.dev · support@allank.dev
Price: $39 launch (list $49) · Refund window: 14 days

---

## Titles

**Primary**

idlescan Desk — A Local Airdrop Research Desk for EVM Wallets

**Alternates**

1. idlescan Desk — Find Idle Balances and Unclaimed Airdrops, Locally
2. idlescan Desk — Self-Hosted Airdrop Research for 14 EVM Chains

## Subtitle

A self-hosted Python desk that reads balances, checks merkle claims against
on-chain roots, and ranks what is worth claiming after gas — without ever holding
a key.

---

## Product description

Most "airdrop checkers" ask a distributor one question: *was index N claimed?*
That answers "not claimed" for almost every address on earth. idlescan Desk
separates the two questions that matter:

1. **Was this address owed anything?** From a published merkle dataset, a
   per-address claim API, or an on-chain allocation view — declared in a
   reviewable registry.
2. **Was that allocation already taken?** Read live from the distributor's
   `isClaimed(index)` or `claimed(address)`.

Then it answers the practical question: **what is left after gas?** Positions are
ranked by net USD after an EIP-1559-aware fee quote, not by headline token value,
and every claim carries a label for how complete that estimate is. Anything the
tool cannot verify is shown as `unknown`, `no eligibility source`, or an explicit
error. It never reports an unverifiable position as zero.

Everything runs on your machine: a Python package with **zero runtime
dependencies**, a local SQLite database, a command-line interface, a documented
JSON API, and a hand-written web interface with no build step and no external
assets. Read-only is the default. One `.env` switch unlocks an optional
**unsigned** claim hand-off for your own browser wallet; the server never holds
private keys, never signs, and never broadcasts.

idlescan Desk is a research tool. It does not promise that any airdrop exists, is
claimable, or is worth claiming.

---

## What you get

- **A local web desk** with eight main views — Overview, Address check, Airdrops,
  Claims, Wallets, Alerts, Activity, Settings — plus a profile page for every
  airdrop and every tracked wallet.
- **A command-line interface** with 18 top-level commands, including `scan`,
  `check`, `claims prepare`, `dataset`, `doctor`, `db`, `backup`, `calendar` and
  `about`.
- **14 EVM chains** and 58 curated tokens configured out of the box, with
  per-chain RPC overrides and optional quorum reads across two endpoints.
- **Merkle proof verification** against the distributor's on-chain root before a
  position is reported, including automatic detection of common leaf encodings.
- **An explicit gas model**: EIP-1559 base and priority fees, a configurable
  buffer, L1 data fees where a chain exposes an oracle, and a quality label
  (`full`, `partial`, `unavailable`) on every claim.
- **Immutable snapshots** with side-by-side diffs, so you can see what changed
  between scans instead of trusting a moving number.
- **Alerts** for deadlines inside 14 days, positions below gas, invalid proofs,
  new allocations, confirmed claims and missing datasets.
- **Exports** in JSON, CSV and Markdown, per snapshot or per airdrop, plus an
  iCalendar deadline feed.
- **A documented JSON API** with 38 routes, optional bearer-token auth,
  per-client rate limits, request size limits, a lockdown mode and security
  headers on every response.
- **Defensive data handling**: an SSRF-safe fetcher for remote datasets (HTTPS
  only, private and metadata addresses blocked, redirects re-checked, size and
  time caps), streaming reads for large claim files, and provenance records.
- **Operational basics**: ordered schema migrations with a mandatory pre-migration
  backup, retention pruning, backup rotation, structured redacted logs, a
  `doctor` command, and `idlescan about` for support.
- **Demo mode and an offline self-test** so you can see every screen populated
  before you add a wallet.

## How it works

1. **Install and open the desk.** Install the wheel into a virtual environment,
   run `idlescan selftest`, then `idlescan serve`. The interface opens at
   `http://127.0.0.1:8787`.
2. **Add a wallet and scan.** Add a watch-only address, choose chains, and run a
   scan. idlescan reads native and curated token balances, prices them, checks
   each configured airdrop's claim state, and stores an immutable snapshot.
3. **Decide with the numbers in front of you.** Sort claims by net after gas,
   open a drop profile to check provenance and proof status, export the snapshot,
   and — only if you enable it — hand an unsigned transaction to your own wallet.

## Trust and safety

- No private keys, seed phrases, keystrokes or signatures are requested or stored.
- No transaction is signed or broadcast by idlescan.
- No token approvals are generated; a legitimate claim needs none.
- The server binds loopback by default and refuses a non-loopback bind without a
  token or an explicit, loudly warned override.
- Zero runtime dependencies: only the Python standard library.
- Local data only: SQLite, snapshots, audit log and backups stay on your disk. No
  telemetry, analytics or update check.
- Unknown stays unknown. Missing prices, failed RPC calls and absent datasets are
  reported as unknown, never as zero.
- Every claim is checked against the on-chain merkle root before it is shown.

The full list of hosts the tool may contact, and commands to audit the package
yourself, are in `TRUST.md` inside the download.

## What is included

```
idlescan-desk-1.2.0/
  START_HERE.txt
  LICENSE
  NOTICE
  THIRD_PARTY.md
  README.md
  QUICKSTART.md
  TRUST.md
  SUPPORT.md
  CHANGELOG.md
  CHECKSUMS.txt
  idlescan-1.2.0-py3-none-any.whl
  idlescan-1.2.0.tar.gz
  examples/airdrops.example.json
  examples/.env.example
```

## What is NOT included

- **No airdrop dataset.** The bundled registry has one entry (Uniswap UNI) and
  ships without its distribution file, because that file is published by the
  project. You supply datasets for real eligibility checks. Until you do, the
  entry reports `no eligibility source` rather than inventing an answer.
- **No auto-claiming.** idlescan never signs or sends a transaction. The optional
  hand-off returns an unsigned transaction to your own wallet.
- **No custody.** It cannot hold, move, or recover funds.
- **No financial, legal or tax advice.** No statement here is a recommendation.
- **No hosted service.** This is a downloadable, self-hosted product, not a
  subscription or a cloud account.
- **No non-EVM chains.** Solana, Cosmos and similar are out of scope.
- **No token-discovery indexer.** Balances come from a curated token list plus
  your own additions, not from an indexer.
- **No promise of future features.** The 1.x roadmap is a direction, not a
  commitment.

## Requirements

- Python 3.10 or newer (3.12 recommended).
- macOS, Linux, or Windows. The release build was verified on Linux (x86-64,
  Python 3.12); Windows and macOS commands are documented but were not executed
  for this release.
- About 60 MB of disk for the install and a growing SQLite database.
- Internet access for RPC reads and price lookups. Demo mode and `selftest` need
  no network.
- No other Python packages. No Node, no Docker, no database server.

## FAQ

**1. Does idlescan claim airdrops for me?**
No. It researches eligibility and, if you explicitly enable write mode, returns
an unsigned transaction for your own wallet. You review and sign.

**2. Does it need my private key or seed phrase?**
Never. It has no field, endpoint or code path that accepts either.

**3. Why does my airdrop show "no eligibility source"?**
The registry entry has no dataset, claim API or allocation view configured yet.
Add the project's published distribution file or endpoint; see `QUICKSTART.md`.

**4. Can it find airdrops I do not know about?**
It checks the drops in your registry. It does not scan the chain for unknown
allocations, and it does not claim to.

**5. Is the gas number exact?**
No. It is estimated from `eth_estimateGas` and current fee data, includes L1 data
fees only where the chain exposes an oracle, and is labelled `full`, `partial` or
`unavailable`. It will differ from the final cost.

**6. Can I run it on a server?**
Yes, with care. Keep it on loopback, or set `IDLESCAN_AUTH_TOKEN` and put TLS in
front. A non-loopback bind without a token is refused unless you explicitly
override it. Write mode has a second, separate remote guard.

**7. Does it send my data anywhere?**
Only read-only RPC calls to the endpoints you configure and price lookups to
DefiLlama (with a CoinGecko fallback). No telemetry. `TRUST.md` lists every host.

**8. What happens when the schema changes?**
Ordered migrations run automatically and take a backup first. `idlescan db status`
shows the schema and pending migrations before you start the server.

**9. Can I get a refund?**
Yes, within 14 days of purchase, through Gumroad. See the refund policy below.

## Refund policy

If idlescan Desk is not what you expected, ask for a refund within **14 days** of
purchase. Email support@allank.dev with your Gumroad order reference. You do not
need to explain why. Refunds are issued through Gumroad under its refund process.
This does not limit any rights you have under applicable consumer law.

## Disclaimer

idlescan Desk is a research tool, not financial, investment, legal, accounting or
tax advice. It does not guarantee that any airdrop exists, is claimable, is still
open, or is worth claiming. Values, gas estimates, eligibility results and
deadlines are approximations derived from third-party data and may be wrong,
incomplete or out of date. You are responsible for verifying every contract
address, distributor, token and transaction against the project's official
sources before you sign anything. Never share a private key or seed phrase with
any tool.

## Founding Release

This is the Founding Release (v1.2.0). Buyers receive updates within the 1.x line
when they are published, at the seller's discretion. The roadmap describes themes
under consideration; it is not a schedule and not a promise. Early buyers get the
current build, the documentation in this folder, and the same terms as later
buyers.

## Suggested tags

`airdrop`, `ethereum`, `evm`, `merkle`, `web3`, `self-hosted`, `python`,
`portfolio`, `research`, `privacy`

## Post-purchase receipt message

```
Thanks for buying idlescan Desk.

1. Download idlescan-desk-1.2.0.zip and compare its SHA-256 with the value on the
   product page. Then verify every file against CHECKSUMS.txt (QUICKSTART.md,
   section 3).
2. Create a virtual environment and install the wheel:
     python -m venv .venv
     (activate it)
     python -m pip install idlescan-1.2.0-py3-none-any.whl
3. Run:  idlescan selftest
   Then: idlescan serve --demo   and open http://127.0.0.1:8787

QUICKSTART.md covers a first real scan, datasets and RPC configuration.
Support: support@allank.dev (48-hour first response).
This is a research tool, not financial advice.
```
