# Trust, privacy and the network

This page states exactly what idlescan Desk can do, what it cannot do, and which
hosts it may contact. Every audit command below was run against the release
build; the real output is in `release/VERIFICATION.md` in the source repository.

## The guarantees

1. **No custody.** idlescan never asks for, accepts, stores, or transmits a
   private key, seed phrase, keystore file, or hardware-wallet signature.
2. **No signing.** It does not sign transactions, typed data, or messages.
3. **No broadcasting.** It does not send raw transactions to any node.
4. **No token approvals.** Claim preparation returns calldata that a legitimate
   `claim(...)` call needs. It never produces `approve`, `permit`, or
   `setApprovalForAll`.
5. **Loopback by default.** The server binds `127.0.0.1`. A non-loopback bind is
   refused unless you set `IDLESCAN_AUTH_TOKEN` or explicitly accept the risk
   with `IDLESCAN_ALLOW_INSECURE_REMOTE=true`.
6. **Zero runtime dependencies.** The product imports only the Python standard
   library. `pip show idlescan` reports an empty `Requires:` field.
7. **Local data.** The SQLite database, snapshots, audit log, and backups stay on
   your machine. Nothing is uploaded by idlescan itself.
8. **Unknown stays unknown.** Missing prices, failed RPC calls, absent datasets
   and unverified proofs are reported as unknown, `no_eligibility_source`, or an
   explicit error — never as zero or as "nothing to claim".

The one deliberate exception to "read-only" is claim hand-off, off by default:
with `IDLESCAN_READ_ONLY=false`, the server returns an **unsigned** transaction
object for your own wallet to review and sign. Local administrative writes
(wallets, airdrops, scans, alerts, backups) happen in either mode unless
`IDLESCAN_LOCKDOWN=true`.

## Hosts idlescan can contact

The only automatic traffic is read-only JSON-RPC over HTTPS and price lookups.
No telemetry, analytics, update check, or crash reporting exists.

### RPC endpoints (shipped defaults, one per listed host)

| chain | hosts |
| --- | --- |
| Ethereum | `ethereum-rpc.publicnode.com`, `eth.drpc.org`, `cloudflare-eth.com` |
| Arbitrum | `arb1.arbitrum.io`, `arbitrum-one-rpc.publicnode.com`, `arbitrum.drpc.org` |
| Optimism | `mainnet.optimism.io`, `optimism-rpc.publicnode.com` |
| Base | `mainnet.base.org`, `base-rpc.publicnode.com` |
| Polygon | `polygon-bor-rpc.publicnode.com`, `polygon.drpc.org` |
| BNB Smart Chain | `bsc-dataseed.binance.org`, `bsc-rpc.publicnode.com` |
| Gnosis | `rpc.gnosischain.com`, `gnosis-rpc.publicnode.com` |
| Avalanche C-Chain | `api.avax.network`, `avalanche-c-chain-rpc.publicnode.com` |
| Scroll | `rpc.scroll.io`, `scroll-rpc.publicnode.com` |
| Linea | `rpc.linea.build`, `linea-rpc.publicnode.com` |
| zkSync Era | `mainnet.era.zksync.io`, `zksync.drpc.org` |
| Celo | `forno.celo.org`, `celo-rpc.publicnode.com` |
| Blast | `rpc.blast.io`, `blast-rpc.publicnode.com` |
| Mode | `mainnet.mode.network`, `mode.drpc.org` |

You can replace any of them. `IDLESCAN_RPC_<CHAIN>=https://...` overrides a
chain; `IDLESCAN_RPC_QUORUM=2` makes critical reads require agreement between two
endpoints.

### Price APIs

| host | purpose |
| --- | --- |
| `coins.llama.fi` | primary USD prices (DefiLlama) |
| `api.coingecko.com` | fallback prices when DefiLlama has no quote |

Disable price lookups entirely with `idlescan scan --no-prices`. Unpriced assets
are reported as unpriced, not zero.

### Hosts you choose

These are never contacted unless you configure them:

- **`dataset_url` eligibility sources** — the URL template you put in a registry
  entry. HTTPS only; private, loopback, link-local, multicast and cloud-metadata
  addresses are blocked, redirects are re-validated, responses are capped.
- **`idlescan registry import https://...`** — the registry file you ask it to
  fetch, through the same SSRF-safe client.
- **`IDLESCAN_NOTIFY_WEBHOOK_URLS`** — alert summaries you opt into. Disabled by
  default. Payloads are minimised and URLs are redacted from logs.
- **Block explorer links** in the UI — ordinary links your browser opens when you
  click them. idlescan does not fetch them.

## Audit it yourself

Run these from the virtual environment where idlescan is installed.

**1. Show what is installed and that there are no dependencies.**

```bash
pip show idlescan
```

Look for `Requires:` with nothing after it.

**2. Prove there is no signing or key-handling code.**

```bash
python -c "import pathlib,idlescan; p=pathlib.Path(idlescan.__file__).parent; bad=['eth_sendRawTransaction','eth_sign','personal_sign','eth_signTypedData','privateKey','private_key','mnemonic']; hits=sorted({(f.relative_to(p).as_posix(),s) for f in p.rglob('*') if f.suffix in {'.py','.js','.html'} for s in bad if s.lower() in f.read_text(errors='ignore').lower()}); print(hits or 'no signing or key-handling symbols found')"
```

Expected output: `no signing or key-handling symbols found`.

**3. List every shipped RPC host.**

```bash
python -c "import json,pathlib,idlescan; from urllib.parse import urlsplit; d=json.loads((pathlib.Path(idlescan.__file__).parent/'data'/'chains.json').read_text()); print('\n'.join(sorted({urlsplit(u).hostname for c in d['chains'] for u in c['rpcs']})))"
```

Compare the result with the table above.

**4. Confirm the bind and mode.**

```bash
idlescan about --json
```

`remote_guard` should say `loopback-only` unless you changed it. `claim_handoff`
should say `disabled` unless you set `IDLESCAN_READ_ONLY=false`.

**5. Watch the network yourself (optional).**

On macOS/Linux, `sudo tcpdump -n -i any 'tcp port 443'` or Little Snitch will show
every connection while you run a scan. Stop the scan and the traffic stops.

## What this does not protect you from

- A malicious **registry entry or dataset** can point at the wrong distributor.
  Proof verification only proves the data matches the root you asked about. Check
  the contract address against the project's official documentation.
- A compromised **RPC provider** can withhold or delay data. Quorum mode and a
  second provider reduce this; neither eliminates it.
- Your **browser wallet** is outside idlescan. It decides what it shows you and
  what it signs.
- idlescan does not audit the airdrop project, its contracts, or its legality.

If any of that is unacceptable, do not sign anything idlescan prepares. Reading
and exporting data carries no on-chain risk.
