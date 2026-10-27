# Quickstart

This guide gets a buyer from the zip file to a working local instance, then to a
first real scan. Every command below was run on the release build before this
document shipped; see `release/VERIFICATION.md` in the source repository.

**idlescan Desk is a research tool, not financial advice. Verify contract
addresses against official sources.**

---

## 1. What is in the folder

```
idlescan-desk-1.2.0/
  START_HERE.txt
  LICENSE, NOTICE, THIRD_PARTY.md
  README.md, QUICKSTART.md, TRUST.md, SUPPORT.md, CHANGELOG.md
  CHECKSUMS.txt
  idlescan-1.2.0-py3-none-any.whl
  idlescan-1.2.0.tar.gz
  examples/airdrops.example.json
  examples/.env.example
```

The wheel is the product. The source archive is included so you can read or audit
the code. You do not need to build anything.

## 2. Requirements

- **Python 3.10 or newer** (3.12 recommended). Check with `python --version`.
- No other packages. idlescan has **zero runtime dependencies**.
- Internet access for RPC calls and price lookups. The demo mode and `selftest`
  need no network at all.
- Disk: about 60 MB including a growing SQLite database.

Platform commands differ only in how you create and activate a virtual
environment.

## 3. Verify the download (recommended)

The product page publishes the SHA-256 of the zip. Compare it, then verify every
file inside the folder against `CHECKSUMS.txt`.

**macOS / Linux**

```bash
shasum -a 256 idlescan-desk-1.2.0.zip        # or: sha256sum ...
cd idlescan-desk-1.2.0
python3 - <<'PY'
import hashlib, pathlib, sys
bad = []
for line in pathlib.Path("CHECKSUMS.txt").read_text().splitlines():
    if not line.strip():
        continue
    digest, name = line.split(None, 1)
    actual = hashlib.sha256(pathlib.Path(name.strip()).read_bytes()).hexdigest()
    if actual != digest:
        bad.append(name)
print("all files verified" if not bad else f"MISMATCH: {bad}")
sys.exit(1 if bad else 0)
PY
```

**Windows PowerShell**

```powershell
Get-FileHash .\idlescan-desk-1.2.0.zip -Algorithm SHA256
cd .\idlescan-desk-1.2.0
python - <<'PY'
import hashlib, pathlib, sys
bad = []
for line in pathlib.Path("CHECKSUMS.txt").read_text().splitlines():
    if not line.strip():
        continue
    digest, name = line.split(None, 1)
    actual = hashlib.sha256(pathlib.Path(name.strip()).read_bytes()).hexdigest()
    if actual != digest:
        bad.append(name)
print("all files verified" if not bad else f"MISMATCH: {bad}")
sys.exit(1 if bad else 0)
PY
```

## 4. Install into a virtual environment

Keep the product in its own environment. Pick a data folder you can write to —
idlescan stores its SQLite database there, not in the package directory.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install idlescan-1.2.0-py3-none-any.whl
mkdir -p ~/idlescan-data
```

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install idlescan-1.2.0-py3-none-any.whl
mkdir $HOME\idlescan-data
```

If PowerShell refuses to run the activation script, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for this window only.

## 5. First checks

```bash
idlescan about            # versions, schema, mode — safe to paste into support
idlescan selftest         # full offline pipeline against a mock chain
```

`selftest` must end with "This demo used a mock chain with zero network calls."
It does **not** prove that your RPC endpoint works.

## 6. Demo mode (no wallet, no network)

```bash
idlescan serve --demo
```

Open <http://127.0.0.1:8787>. Every screen is populated with synthetic data.
Stop the server with `Ctrl-C`.

Use demo mode to learn the interface before touching a real address.

## 7. Configure the database and an RPC endpoint

Create a `.env` file in the folder where you will run idlescan:

```dotenv
# Where your research data lives.
IDLESCAN_DB=~/idlescan-data/idlescan.db

# A provider URL you are entitled to use. Public endpoints work for light use.
IDLESCAN_RPC_ETHEREUM=https://ethereum-rpc.publicnode.com

# Keep the safe default: read-only. See README for the claim hand-off switch.
IDLESCAN_READ_ONLY=true
```

On Windows use a Windows path such as `C:\idlescan-data\idlescan.db`.

Relative `IDLESCAN_DB` paths resolve against the directory that contains the
`idlescan` package, so an absolute path is the least surprising choice. The
`idlescan about` output shows the database filename in use.

Check before scanning:

```bash
idlescan doctor --offline   # configuration, database, registry — no network
idlescan doctor --online    # additionally probes each configured RPC endpoint
```

## 8. Add a wallet and run a first scan

```bash
idlescan wallets add 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 --label "main"
idlescan wallets list
idlescan scan --saved --save --track
```

`--saved` scans tracked wallets, `--save` stores an immutable snapshot, `--track`
records the scan in the audit log. Add `--chains ethereum,base` to limit the
work, or `--all` to include addresses with no allocation.

Then start the server and browse the snapshot:

```bash
idlescan serve
```

Without `--demo` the UI shows your own data. The default bind is loopback only.

## 9. Add an airdrop and a dataset

The bundled registry contains **one** entry: Uniswap (UNI). It ships **without**
its merkle dataset, because the distributor's distribution file is published by
the project, not by idlescan. Until you supply that file, the entry reports
`no_eligibility_source` — it never pretends there is nothing to claim.

**Add an entry.** Use the UI (**Airdrops → Add airdrop**) or the API. The CLI
validates a registry file but does not install it:

```bash
idlescan registry export my-registry.json
# edit my-registry.json, then validate it against chains and the schema
idlescan registry import my-registry.json
```

**Add a dataset.** Put the claims JSON in a writable folder and point idlescan at
it:

```dotenv
IDLESCAN_DATASET_DIR=~/idlescan-data/datasets
```

```bash
idlescan dataset ~/idlescan-data/datasets/project.claims.json \
  --chain ethereum --distributor 0xDistributorAddress
```

`idlescan dataset` checks the file's proofs against the distributor's on-chain
`merkleRoot()` before you trust it. A mismatch is an error, not a warning.

See README → "Adding an airdrop" for the three supported eligibility shapes
(published merkle dataset, per-address claim API, on-chain allocation view).

## 10. Where your data lives

- **Database:** the path in `IDLESCAN_DB` (default `data/idlescan.db` under the
  current working directory). One SQLite file plus `-wal`/`-shm` while running.
- **Backups:** `idlescan backup --out <folder>` or Settings → Maintenance.
  Backups are local; nothing is uploaded.
- **Exports:** `idlescan export --format json|csv|md --out <path>`.
- **Datasets:** wherever `IDLESCAN_DATASET_DIR` points.

Copy the database file only while the server is stopped, or use the backup
command, which uses SQLite's online backup API.

## 11. Common problems

| symptom | cause and fix |
| --- | --- |
| `error: refusing to start on non-loopback host` | You bound to `0.0.0.0` without a token. Use `127.0.0.1`, set `IDLESCAN_AUTH_TOKEN`, or accept the risk with `IDLESCAN_ALLOW_INSECURE_REMOTE=true`. |
| `database is locked` | Two scans started at once, or a backup is running. Wait, then retry; scans are single-flight by design. |
| every claim says `no_eligibility_source` | The drop has no dataset yet. Add one as in step 9. |
| every claim says `unknown` | RPC reads failed. Run `idlescan doctor --online`. |
| `ssl` or DNS errors | Your provider URL is wrong or blocked. Try another endpoint in `.env`. |
| browser shows a blank page | The server failed to start; read the terminal output. `idlescan about --json` plus the exact error is what support needs. |

Never send private keys, seed phrases, or RPC URLs containing keys to support.
