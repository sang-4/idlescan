# Roadmap

Themes under consideration for future 1.x releases. This is a plan sketch, not a
promise: order, scope and dates are not committed, and some items may never ship.
Nothing here is part of the 1.2.0 release.

## Reliability and data quality

- Broader L2 fee coverage: native L1-fee oracles on more chains, with an explicit
  confidence value per chain.
- Dataset distribution helpers: documented provenance sidecars, published
  benchmarks for large files, and a guided importer for common distributor forks.
- Token discovery beyond the curated list, with bounded, opt-in heuristics and
  spam filtering. Off by default.

## Security

- Token Lists import with signature and schema validation, plus Multicall3
  batching with a fallback path.
- A formal threat-model review of the claim hand-off path and the SSRF client.
- Signed release artefacts and reproducible builds where the toolchain allows.

## Operations

- Read-only follower mode for a second machine, with explicitly non-custodial,
  one-way snapshot export.
- Richer notification routing (per-alert-kind channels, quiet hours) without
  weakening the redaction rules.
- Optional desktop packaging around the existing local server. No hosted service
  is planned.

## Interface

- Split the single-file frontend into ES modules with a build-free loader.
- Surface the data already in the API: gas-quality badges, preflight results,
  RPC health, dataset provenance, custom-token management, and scan cancellation.
- First-run checklist and a light/dark theme toggle.

## Explicitly out of scope

- Holding, signing, or broadcasting anything.
- Non-EVM chains.
- Hosted or multi-tenant operation.
- Any claim about airdrop gains, returns, or assured eligibility.
