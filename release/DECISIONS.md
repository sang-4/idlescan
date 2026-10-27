# Release decisions

One line each. "Owner" marks a decision the owner made during this sprint.

1. **Ship the current hardened tree as v1.2.0**, not a rebuilt 1.1.1 from the
   `v1.1.0-baseline` tag. Owner decision, taken after the discrepancy was
   surfaced: the workspace already contained the completed v1.2 work.
2. **Product name is "idlescan Desk"; package and command stay `idlescan`.**
   Avoids breaking imports, the console script, and every documented command.
3. **Support address is support@allank.dev.** Owner-provided at the start of the
   sprint, replacing the `SUPPORT_EMAIL_HERE` placeholder.
4. **The licence is a plain-language draft**, not legal advice. The owner should
   have counsel review it before relying on it.
5. **GPL-3.0 attribution corrected.** The Uniswap fixture was described as "MIT"
   in the README and one test docstring; it is now GPL-3.0 in `THIRD_PARTY.md`
   and both files. The fixture remains test-only and is excluded from the
   archive, wheel and sdist.
6. **Default database location changed** to `<cwd>/data/idlescan.db` when
   `IDLESCAN_DB` is unset, so a wheel installed into site-packages can still
   write. Explicit relative paths keep resolving against the project root.
   Documented in the changelog and QUICKSTART.
7. **CSV gas note is a single trailer row** with `kind=note` and
   `asset_or_drop=gas_estimate_note`, keeping the header first and the note
   report-level rather than per-row.
8. **`/api/config` gains three additive fields**: `insecure_remote`,
   `gas_estimate_note`, `disclaimer`.
9. **The insecure-remote banner cannot be dismissed**; all other banners keep
   their dismiss button. An insecure bind should stay visible.
10. **`about` reduces an absolute database path to its filename** and never emits
    RPC URLs, tokens, environment values or wallet addresses.
11. **Legacy `idlescan tokens --chain …` flags restored** on the parent parser;
    `tokens list` continues to work. This reverses an accidental v1.2 CLI break.
12. **`idlescan --version` added** at the top level; it exits 0 via argparse's
    version action.
13. **Build uses `python -m build` when available**, otherwise `pip wheel` plus a
    standard-library source tarball. No new runtime dependency.
14. **The release zip is deterministic**: sorted entries, fixed 2026-01-01
    timestamps, mode 0644.
15. **Copy checker exemptions**: `LICENSE`, `NOTICE`, `THIRD_PARTY.md` (legal and
    attribution text) and `BANNED_WORDS.md` (the definition list itself), plus
    lines containing PowerShell's `Set-ExecutionPolicy`.
16. **Secret scanner skips binary members** and recurses into the wheel and sdist.
    Credential detection looks at quoted literals and dotenv-style lines, so
    identifiers such as `token = self.settings.auth_token` do not false-positive.
17. **Verification used `virtualenv`**, because `python3 -m venv` fails on this
    host without `ensurepip`. This is a tooling deviation, not a product one.
18. **Screenshots were not captured**: no headless or desktop browser is
    installed. The owner captures them from `SHOTLIST.md`.
19. **R-13 required no new code**: the SSRF-safe client already shipped in v1.2
    and is covered by `tests/test_safe_http.py`.
20. **Windows and macOS were not executed.** Only Linux 6.18 (WSL2) with Python
    3.12.3 was available, so the listing says exactly that.
21. **Inline `style=` attributes remain in the UI.** The CSP is strict for
    scripts (`script-src 'self'`, no `unsafe-eval`) and allows inline styles
    because the existing interface uses CSS variables inline. Removing them is a
    v1.3 interface task and is listed as a known limitation.
22. **`PROJECT_ANALYSIS.md` stays in the repository** as internal working notes
    and is not part of the release archive.
23. **`docker build` was not re-run** after the final tree changes; container
    files are shipped as documentation, not as verified artefacts.
