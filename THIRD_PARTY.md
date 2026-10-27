# Third-party components

idlescan Desk is written by Allan Kiprop and ships with **no third-party runtime
code**. At runtime it imports only the Python standard library. Third-party
packages used for development (ruff, mypy, build) are listed in
`pyproject.toml` under the `dev` extra and are never imported by the product.

## Test-only fixture

`tests/fixtures/uniswap_complex_example.json` comes from the
[Uniswap/merkle-distributor](https://github.com/Uniswap/merkle-distributor)
repository, which is licensed under **GPL-3.0**. It contains a sample merkle
tree used to check that idlescan's proof and root calculations agree with a
known-good implementation.

- Used only by the offline test suite (`tests/test_merkle.py`).
- Not imported, read, or required at runtime.
- **Not included in the distributed release archive, wheel, or source
  distribution.** The release build script fails if it is present.

## Data files

`idlescan/data/chains.json`, `idlescan/data/tokens.json`, and
`idlescan/data/airdrops.json` are configuration written for this project. They
contain public contract addresses, public RPC endpoints, and public token
metadata, not copied third-party code.

## Services contacted at runtime

The software can make read-only requests to the RPC endpoints configured in
`chains.json` and to the DefiLlama price API. No data is uploaded. See TRUST.md
for the full list and the commands to audit it.
