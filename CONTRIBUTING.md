# idlescan Contributing Guide

## Development Setup

```bash
git clone https://github.com/sang-4/idlescan
cd idlescan
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the test suite:

```bash
make test          # python -m unittest discover -s tests
make selftest      # python -m idlescan selftest
make lint          # ruff check + ruff format --check
make typecheck     # mypy
make doctor        # python -m idlescan doctor --offline
```

## Code Standards

- **Python**: PEP 8, type hints on public functions, docstrings explaining *why*
- **No runtime dependencies**: standard library only; dev tools under
  `[project.optional-dependencies].dev`
- **No build step**: frontend is plain static files served by the stdlib server
- **No secrets in logs/API**: all URLs/tokens are redacted
- **Non-custodial**: never store keys, never sign, never broadcast

## Commit Convention

Use conventional commits:

```
feat(scope): description
fix(scope): description
sec(scope): description
docs(scope): description
ci(scope): description
test(scope): description
refactor(scope): description
```

Each commit must leave the test suite green.

## Adding a Registry Entry

1. Copy `idlescan/data/airdrops.json` to a working file
2. Add your entry under `airdrops` (see `docs/REGISTRY_CONTRIBUTING.md`)
3. Run `idlescan registry import <file>` to validate
4. Run `idlescan verify --registry <file>` to check against chains
5. Submit a pull request

**Do not invent** distributor addresses, token addresses, or deadlines. Every
entry must be verifiable from a primary source.

## Documentation

- `docs/CONFIGURATION.md` — every environment variable
- `docs/OPERATIONS.md` — deployment, backup, upgrades
- `docs/ARCHITECTURE.md` — layer diagram, module map, data flow
- `docs/API.md` — all API routes
- `docs/DECISIONS.md` — architectural decisions
- `docs/IMPLEMENTATION_PLAN.md` — work package status
- `SECURITY.md` — threat model and guarantees

Doc-sync tests verify that API routes and environment variables are documented.

## License

Proprietary single-user license, copyright 2026 Allan Kiprop. See `LICENSE`.
Contributions are accepted only under a signed contributor agreement; open an
issue first. The GPL-3.0 Uniswap fixture in `tests/fixtures/` is test-only and
excluded from the built distribution -- see `THIRD_PARTY.md`.
