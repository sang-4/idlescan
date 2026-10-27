# Release report — idlescan Desk 1.2.0 (Founding Release)

- **Seller:** Allan Kiprop (individual), https://allank.dev
- **Support:** support@allank.dev
- **Release date:** 2026-10-27
- **Price:** $39 launch (list $49), 14-day refund window
- **Licence:** proprietary single-user (draft; see owner actions)
- **Archive:** `release/dist/idlescan-desk-1.2.0.zip`, 419,200 bytes
- **SHA-256:** `928e4979d47a31ee5674863aa3f6804dc130e3dc528150cfa970b459ecc990df`

> **Scope decision.** The brief assumed a v1.1.0 baseline with 148 tests and a
> non-git workspace. The actual workspace was already a git repository on branch
> `dev` at v1.2.0 with the full hardening work committed, 362 tests, and schema
> v2. The owner was asked and chose to **ship the current tree as v1.2.0**, not to
> rebuild a smaller 1.1.1 from the `v1.1.0-baseline` tag. Every R-item is therefore
> applied to v1.2.0, and the version in the brief's table is superseded by that
> decision.

## Status

| Item | Status | Commit | Evidence |
| --- | --- | --- | --- |
| R-01 Licence and seller identity | Complete | `3c3ec07` | `LICENSE`, `NOTICE`, `THIRD_PARTY.md`; pyproject licence/classifier/author/URLs; `tests/test_release_meta.py` |
| R-02 Version and changelog | Complete | `3c3ec07` | version 1.2.0 in `pyproject.toml` + `idlescan/__init__.py`; matching-version test; `CHANGELOG.md` 1.2.0 |
| R-03 Honest gas labelling | Complete | `3c3ec07` | `GAS_ESTIMATE_NOTE` in `idlescan/gas.py`; CLI footer, JSON, CSV trailer, Markdown, `/api/config`, Claims view; `tests/test_report.py` |
| R-04 In-product disclaimer | Complete | `3c3ec07` | `DISCLAIMER` in CLI/Markdown; Settings view; README; `tests/test_report.py`, `tests/test_api.py` |
| R-05 Network exposure guard | Complete | `3c3ec07` | `idlescan/config.py` `validate()`; startup warning; non-dismissable UI banner; loopback/refusal/override/exit-2 tests |
| R-06 `idlescan about` | Complete | `3c3ec07` | `idlescan/about.py`, `cmd_about`; 10 tests incl. a secret-in-environment test |
| R-07 Release build script and archive | Complete | `b427647` | `scripts/build_release.py`; deterministic zip + `.sha256`; excluded-file and placeholder gates |
| R-08 Buyer documentation | Complete | `b427647` | `QUICKSTART.md`, `TRUST.md`, `SUPPORT.md`, `docs/ROADMAP.md`; honest data section in README/QUICKSTART |
| R-09 Documentation drift | Complete | `b427647` | README auth/licence/counts; `keccak.py` reference verified; counts re-derived from source |
| R-10 Listing copy and assets | Complete | `b427647` | `release/listing/` (LISTING, SHOTLIST, POSTS, BANNED_WORDS, CLAIMS_EVIDENCE); `scripts/check_copy.py` passes |
| R-11 Fresh-machine verification | Complete | `f5eff5e` | `scripts/verify_release.sh`; `release/VERIFICATION.md` with real output |
| R-12 Secret and leakage scan | Complete | `b427647` | `scripts/scan_release.py`; PASS on archive, wheel and sdist |
| R-13 SSRF reduction | Complete (pre-existing) | `b808f8d` | `idlescan/safe_http.py`; `tests/test_safe_http.py` (private IP, metadata, redirect, oversize, success) |
| R-14 Screenshot capture | **Skipped** | — | No browser installed; owner captures from `SHOTLIST.md` |

## Metrics (measured, not estimated)

| Metric | Value |
| --- | --- |
| Tests | **388 passing**, 1 skipped (`sort_pairs=False` variant) |
| Suite runtime | ~118 s, offline, single core |
| Skipped | 1 |
| Coverage | **Not measured.** No coverage tool is installed and the brief's gates did not require it; no number is claimed. |
| API routes | 38 |
| CLI top-level commands | 18 |
| Chains / curated tokens | 14 / 58 |
| Bundled registry entries | 1 (Uniswap UNI), no bundled dataset |
| DB schema | v2 |
| Runtime dependencies | 0 (`Requires:` is empty in `pip show`) |
| Modules in package | 29 Python files, 11,518 lines |
| Archive | 419,200 bytes, 14 files |
| Wheel / sdist entries | 45 / 63, both free of `tests/` and the GPL fixture |

## Quality gate evidence

**Full suite**

```text
$ python3 -m unittest discover -s tests
Ran 388 tests in 118.217s

OK (skipped=1)
```

**Self-test**

```text
$ python3 -m idlescan selftest
  - gas estimate present: True

This demo used a mock chain with zero network calls.
```

**Build**

```text
$ python3 scripts/build_release.py
archive: .../release/dist/idlescan-desk-1.2.0.zip (419,200 bytes)
sha256:  928e4979d47a31ee5674863aa3f6804dc130e3dc528150cfa970b459ecc990df
```

Archive contents: `START_HERE.txt`, `LICENSE`, `NOTICE`, `THIRD_PARTY.md`,
`README.md`, `QUICKSTART.md`, `TRUST.md`, `SUPPORT.md`, `CHANGELOG.md`,
`CHECKSUMS.txt`, the wheel, the sdist, `examples/airdrops.example.json`,
`examples/.env.example`.

**Leakage scan**

```text
$ python3 scripts/scan_release.py
PASS: no secrets, personal paths or banned words in idlescan-desk-1.2.0.zip
```

**Copy check**

```text
$ python3 scripts/check_copy.py
PASS: scanned 11 files; no banned marketing words
```

**Fresh-machine verification** — full transcript in `release/VERIFICATION.md`.
Highlights: `all files verified`; wheel installs with `--no-index --no-deps`;
`idlescan --version` → `idlescan 1.2.0`; `about --json` contains no secrets;
`selftest` passes; `/api/health`, `/`, `/api/portfolio`, `/api/airdrops`,
`/app.js` all 200 with the full security-header set; `serve --host 0.0.0.0`
exits **2**; both distributions contain no tests or fixture.

**Banned seller string**

```text
$ python3 - (scan every member of the release zip for the banned name)
none
```

**MIT reference sweep**

```text
$ grep -ri "mit license" . --exclude-dir=.git --exclude-dir=__pycache__ \
    --exclude-dir=release --exclude-dir=.mypy_cache
none in repo
```

**Lint** — ruff (`F,E9,B,I`, line-length 119) clean on all new/modified files;
pyflakes clean on `scripts/` and `idlescan/about.py`. mypy remains lenient
repository-wide; the new modules are typed.

## Behaviour changes a v1.1 user will notice

1. **Remote binds are refused by default.** A non-loopback host needs
   `IDLESCAN_AUTH_TOKEN` or `IDLESCAN_ALLOW_INSECURE_REMOTE=true`; the override is
   warned at startup and shows a persistent UI banner. Write mode has a second
   guard (`IDLESCAN_ALLOW_REMOTE_WRITE`).
2. **Licence is proprietary.** The MIT licence and all MIT references are gone.
3. **Default database location** moved from next to the installed package to
   `<cwd>/data/idlescan.db` when `IDLESCAN_DB` is unset. Explicit relative paths
   still resolve against the project root.
4. **CSV exports gained one trailer row** (`kind=note`, `gas_estimate_note`).
   Row-oriented consumers that assumed "every row is a holding or claim" need to
   filter on `kind`.
5. **`idlescan doctor` exits 1 on warnings**, including the claim-hand-off warning.
6. **Demo/test net value** changed from `607.007376` to `606.508114` (buffered gas).
7. **`idlescan --version`** now exists; `idlescan tokens --chain …` works again
   without the `list` subcommand.
8. **`/api/config`** gained `insecure_remote`, `gas_estimate_note`, `disclaimer`.

## Owner action list

1. **Paste `release/listing/LISTING.md` into Gumroad** as the product description,
   set $39 launch / $49 list, 14-day refund.
2. **Capture the eight screenshots** from `release/listing/SHOTLIST.md` and the
   45-second recording. No browser was available here.
3. **Upload `idlescan-desk-1.2.0.zip`** and publish the SHA-256 from
   `idlescan-desk-1.2.0.zip.sha256` on the product page.
4. **Have `LICENSE` reviewed by a lawyer.** It is a plain-language draft, not
   legal advice, and it is not claimed to be sufficient.
5. **Confirm support@allank.dev receives mail**, and that you can meet the
   48-hour first-response promise.
6. **Post the announcements** from `release/listing/POSTS.md` when the page is
   live, not before.
7. **Decide whether to publish the source tarball.** It is in the archive; the
   licence forbids redistribution by the buyer.
8. **Before selling at scale**, smoke-test on Windows and macOS and on Python
   3.10/3.11/3.13. Only Linux + 3.12 was executed here.
9. **Review the default-database change** (`release/DECISIONS.md` item 6), since it
   affects existing v1.1 users who relied on the old default.

## Known limitations to keep honest on the listing page

- No bundled dataset. The single registry entry (Uniswap UNI) reports
  `no_eligibility_source` until the buyer supplies the project's distribution file.
- Gas estimates are approximations. L1 data fees are included only where the chain
  exposes an oracle; the report labels those estimates `partial`.
- Windows and macOS are documented but not executed for this release.
- Only Python 3.12 was executed; 3.10+ is declared, not proven.
- The CSP allows inline **styles** (the existing UI uses inline CSS variables).
  `script-src` is strict and `unsafe-eval` is absent. This deviates from the
  brief's "no inline styles" and is a v1.3 interface task.
- Several v1.2 backend features (gas-quality badges, preflight results, RPC
  health, provenance panel, cancel button, notifications, custom tokens) are
  exposed by the API and CLI but not yet surfaced in the browser UI.
- `docker build` was not re-run on the final tree; container files are
  documentation, not verified artefacts.
- No coverage figure is published because none was measured.

## Deviations and unverified items

| Item | What happened |
| --- | --- |
| Version | Shipped 1.2.0, not 1.1.1 (owner decision, documented above). |
| Test baseline | Brief said 148; the tree had 149 at the v1.1.0 tag and 362 at start of this sprint. |
| R-13 | No new code: the v1.2 SSRF client already met and exceeded the requirement; verified by existing tests. |
| R-14 | Skipped: no headless or desktop browser installed; nothing was installed to work around it. |
| Virtual environment | `python3 -m venv` lacks `ensurepip` on this host, so `virtualenv` was used. |
| Coverage | Not measured; no number invented. |
| Docker / CI | Written earlier in the v1.2 sprint; not re-executed against the final tree. |
| mypy | Repository-wide mypy is lenient and pre-existing code has known errors; new modules are clean. |
| Line-count metric | The earlier v1.2 report's line count was corrected to the measured 11,518. |
