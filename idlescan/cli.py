"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .abi import normalise_address
from . import __version__
from .cache import Cache
from .db import Database
from .dataset import DatasetError, dataset_provenance, parse_dataset_file
from .chains import ConfigError, load_chains, load_tokens
from .prices import PriceOracle
from .registry import load_registry
from .report import export, render_console, render_markdown, to_dict
from .rpc import Rpc
from .scanner import Scanner
from .selftest import describe_invariants, run_selftest
from .config import SettingsError, load_settings
from .service import PlatformService, ServiceError
from .verify import verify_drops

BANNER = "idlescan - airdrop research desk (read-only by default)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idlescan",
        description=BANNER,
        epilog=(
            "idlescan never asks for keys and never signs anything. Claim hand-off is "
            "disabled unless IDLESCAN_READ_ONLY=false is set in .env; even then the "
            "server only returns an unsigned transaction for your own wallet. "
            "Always verify the distributor address in the project's official docs."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="scan wallets for idle balances and unclaimed allocations")
    scan.add_argument("addresses", nargs="*", help="EVM addresses to scan")
    scan.add_argument("-f", "--addresses-file", type=Path, help="file with one address per line (# comments allowed)")
    scan.add_argument("-c", "--chains", default="all", help="comma separated chain keys, or 'all' (default)")
    scan.add_argument("--registry", type=Path, help="airdrops registry JSON (default: bundled)")
    scan.add_argument("--chains-file", type=Path, help="override chain/RPC configuration")
    scan.add_argument("--tokens-file", type=Path, help="override token list")
    scan.add_argument("--dataset-dir", type=Path, help="extra directory to look for claim datasets")
    scan.add_argument("--min-usd", type=float, default=0.0, help="hide balances below this USD value (default 0)")
    scan.add_argument("--no-prices", action="store_true", help="skip price lookups (raw amounts only)")
    scan.add_argument("--no-gas", action="store_true", help="skip gas estimation for claims")
    scan.add_argument("--no-cache", action="store_true", help="ignore the on-disk cache")
    scan.add_argument("--no-verify-tokens", action="store_true", help="trust the token list without on-chain checks")
    scan.add_argument("--all", action="store_true", help="also list addresses with no allocation")
    scan.add_argument("--json", action="store_true", help="print JSON instead of tables")
    scan.add_argument("--out", type=Path, help="export path prefix, e.g. reports/october")
    scan.add_argument("--format", default="", help="comma separated export formats: json,csv,md")
    scan.add_argument("-q", "--quiet", action="store_true", help="only print the final table")
    scan.add_argument("-v", "--verbose", action="store_true")
    scan.add_argument("--save", action="store_true", help="save the result to the local platform database")
    scan.add_argument("--saved", action="store_true", help="scan wallets already tracked in the database")
    scan.add_argument("--track", action="store_true", help="with --save, also add the scanned addresses as wallets")
    scan.add_argument("--db", type=Path, help="database path for --save (default: IDLESCAN_DB or data/idlescan.db)")
    scan.add_argument("--env-file", type=Path, help="path to .env")

    verify = sub.add_parser("verify", help="check registry entries against the chains")
    verify.add_argument("--registry", type=Path)
    verify.add_argument("--chains-file", type=Path)
    verify.add_argument("--json", action="store_true")

    demo = sub.add_parser("selftest", help="run the offline end-to-end demo (no network)")
    demo.add_argument("--json", action="store_true")
    demo.add_argument("--markdown", action="store_true")

    dataset = sub.add_parser(
        "dataset", help="validate a merkle claims file against a distributor contract"
    )
    dataset.add_argument("path", type=Path, help="claims JSON to validate")
    dataset.add_argument("--distributor", required=True, help="distributor contract address")
    dataset.add_argument("--chain", required=True, help="chain key for the contract")
    dataset.add_argument("--root", help="compare against this root instead of reading the chain")
    dataset.add_argument("--root-method", default="merkleRoot()", help="view method returning the root")
    dataset.add_argument("--all-proofs", action="store_true", help="verify every proof, not a sample")
    dataset.add_argument("--chains-file", type=Path)
    dataset.add_argument("--json", action="store_true")

    check = sub.add_parser("check", help="check one address without tracking it")
    check.add_argument("address", help="EVM address to check")
    check.add_argument("--json", action="store_true", help="print the full JSON result")
    check.add_argument("--env-file", type=Path)
    check.add_argument("--db", type=Path)

    chains = sub.add_parser("chains", help="list configured chains and RPC endpoints")
    chains.add_argument("--chains-file", type=Path)
    chains.add_argument("--json", action="store_true")

    tokens = sub.add_parser("tokens", help="list configured tokens")
    tokens.add_argument("--chain", help="only this chain")
    tokens.add_argument("--tokens-file", type=Path)
    tokens.add_argument("--chains-file", type=Path)
    tokens.add_argument("--json", action="store_true")

    serve = sub.add_parser("serve", help="run the web UI and JSON API")
    serve.add_argument("--host", help="bind address (default: IDLESCAN_HOST or 127.0.0.1)")
    serve.add_argument("--port", type=int, help="port (default: IDLESCAN_PORT or 8787)")
    serve.add_argument("--db", type=Path, help="SQLite database path")
    serve.add_argument("--demo", action="store_true", help="seed a synthetic demo portfolio (no network)")
    serve.add_argument("--env-file", type=Path, help="path to .env")

    wallets = sub.add_parser("wallets", help="manage the tracked-wallet registry")
    wallet_sub = wallets.add_subparsers(dest="wallet_command", required=True)
    wallet_add = wallet_sub.add_parser("add", help="track an address")
    wallet_add.add_argument("address")
    wallet_add.add_argument("--label", default="", help="human label, e.g. 'cold wallet'")
    wallet_add.add_argument("--tags", default="", help="comma separated tags")
    wallet_add.add_argument("--json", action="store_true")
    wallet_add.add_argument("--env-file", type=Path)
    wallet_add.add_argument("--db", type=Path)
    wallets.add_argument("--env-file", type=Path)
    wallets.add_argument("--db", type=Path)

    wallet_list = wallet_sub.add_parser("list", help="list tracked wallets")
    wallet_list.add_argument("--json", action="store_true")
    wallet_list.add_argument("--env-file", type=Path)
    wallet_list.add_argument("--db", type=Path)

    wallet_rm = wallet_sub.add_parser("rm", help="stop tracking an address")
    wallet_rm.add_argument("address")
    wallet_rm.add_argument("--env-file", type=Path)
    wallet_rm.add_argument("--db", type=Path)

    claims = sub.add_parser("claims", help="inspect claim positions from the latest snapshot")
    claim_sub = claims.add_subparsers(dest="claim_command", required=True)
    claim_list = claim_sub.add_parser("list", help="list claim positions")
    claim_list.add_argument("--all", action="store_true", help="include claimed and expired positions")
    claim_list.add_argument("--wallet", help="only this address")
    claim_list.add_argument("--min-net", type=float, help="only positions at or above this net USD value")
    claim_list.add_argument("--json", action="store_true")
    claim_list.add_argument("--env-file", type=Path)
    claim_list.add_argument("--db", type=Path)

    claim_prepare = claim_sub.add_parser(
        "prepare", help="return an unsigned claim transaction (requires write mode)"
    )
    claim_prepare.add_argument("drop_id")
    claim_prepare.add_argument("wallet")
    claim_prepare.add_argument("--json", action="store_true")
    claim_prepare.add_argument("--env-file", type=Path)
    claim_prepare.add_argument("--db", type=Path)

    claim_confirm = claim_sub.add_parser(
        "confirm", help="record a submitted claim transaction (requires write mode)"
    )
    claim_confirm.add_argument("drop_id")
    claim_confirm.add_argument("wallet")
    claim_confirm.add_argument("tx_hash")
    claim_confirm.add_argument("--json", action="store_true")
    claim_confirm.add_argument("--env-file", type=Path)
    claim_confirm.add_argument("--db", type=Path)

    alerts = sub.add_parser("alerts", help="review alerts from the local database")
    alerts.add_argument("--all", action="store_true", help="include acknowledged alerts")
    alerts.add_argument("--ack", type=int, metavar="ID", help="acknowledge one alert")
    alerts.add_argument("--ack-all", action="store_true", help="acknowledge every open alert")
    alerts.add_argument("--json", action="store_true")
    alerts.add_argument("--env-file", type=Path)
    alerts.add_argument("--db", type=Path)

    export = sub.add_parser("export", help="export the latest snapshot")
    export.add_argument("--format", choices=["json", "csv", "md"], default="json")
    export.add_argument("--out", type=Path, help="write to this path instead of stdout")
    export.add_argument("--env-file", type=Path)
    export.add_argument("--db", type=Path)

    backup = sub.add_parser("backup", help="write a consistent copy of the local database")
    backup.add_argument("--out", type=Path, help="destination file or directory (default: data/backups/)")
    backup.add_argument("--json", action="store_true")
    backup.add_argument("--env-file", type=Path)
    backup.add_argument("--db", type=Path)

    db = sub.add_parser("db", help="inspect, migrate, check and prune the local database")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_status = db_sub.add_parser("status", help="show schema version, size and row counts")
    db_status.add_argument("--json", action="store_true")
    db_status.add_argument("--env-file", type=Path)
    db_status.add_argument("--db", type=Path)

    db_migrate = db_sub.add_parser("migrate", help="apply pending schema migrations")
    db_migrate.add_argument("--json", action="store_true")
    db_migrate.add_argument("--env-file", type=Path)
    db_migrate.add_argument("--db", type=Path)

    db_check = db_sub.add_parser("check", help="integrity and consistency checks (read-only)")
    db_check.add_argument("--json", action="store_true")
    db_check.add_argument("--env-file", type=Path)
    db_check.add_argument("--db", type=Path)

    db_prune = db_sub.add_parser(
        "prune", help="delete history outside retention windows (dry-run by default)"
    )
    db_prune.add_argument("--yes", action="store_true", help="actually delete rows")
    db_prune.add_argument("--snapshots", type=int, help="snapshots to keep (default: setting)")
    db_prune.add_argument("--alerts-days", type=int)
    db_prune.add_argument("--audit-days", type=int)
    db_prune.add_argument("--executions-days", type=int)
    db_prune.add_argument("--jobs-days", type=int)
    db_prune.add_argument("--vacuum", action="store_true", help="VACUUM after deleting")
    db_prune.add_argument("--json", action="store_true")
    db_prune.add_argument("--env-file", type=Path)
    db_prune.add_argument("--db", type=Path)

    calendar = sub.add_parser("calendar", help="export airdrop deadlines as an iCalendar feed")
    calendar.add_argument("--drop", help="only this airdrop id")
    calendar.add_argument("--out", type=Path, help="write to this path instead of stdout")
    calendar.add_argument("--env-file", type=Path)
    calendar.add_argument("--db", type=Path)

    return parser


def read_addresses(inline: Sequence[str], path: Path | None) -> list[str]:
    raw: list[str] = list(inline)
    if path:
        try:
            content = path.read_text()
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from None
        for line in content.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                raw.append(line)
    addresses: list[str] = []
    for item in raw:
        for part in item.replace(",", " ").split():
            if not part.strip():
                continue
            try:
                address = normalise_address(part)
            except ValueError as exc:
                raise ConfigError(str(exc)) from None
            if address not in addresses:
                addresses.append(address)
    return addresses


def cmd_scan(args: argparse.Namespace) -> int:
    if args.save or args.saved:
        return cmd_scan_saved(args)
    chains = load_chains(args.chains_file)
    tokens = load_tokens(chains, args.tokens_file)
    drops = load_registry(args.registry, chains)
    addresses = read_addresses(args.addresses, args.addresses_file)
    if not addresses:
        print(
            "no addresses given\n\n"
            "  idlescan scan 0xabc... 0xdef...\n"
            "  idlescan scan -f addresses.txt --chains ethereum,arbitrum\n\n"
            "addresses are public; never paste a seed phrase or private key.",
            file=sys.stderr,
        )
        return 2

    if args.dataset_dir:
        for drop in drops:
            if drop.eligibility and drop.eligibility.dataset:
                current = Path(drop.eligibility.dataset)
                if not current.exists():
                    candidate = args.dataset_dir / current.name
                    if candidate.exists():
                        drop.eligibility.dataset = str(candidate)

    chain_keys = [] if args.chains.strip().lower() in ("", "all", "*") else args.chains.split(",")
    cache = Cache(enabled=not args.no_cache)
    prices = PriceOracle(enabled=not args.no_prices, cache=cache)
    scanner = Scanner(
        chains,
        tokens,
        drops,
        cache=cache,
        prices=prices,
        rpc_factory=lambda chain: Rpc(list(chain.rpcs)),
        verify_tokens=not args.no_verify_tokens,
        estimate_gas=not args.no_gas,
        verbose=(lambda message: print(message, file=sys.stderr)) if args.verbose else None,
    )
    try:
        report = scanner.scan(chain_keys, addresses)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    if args.min_usd > 0:
        report.holdings = [
            h for h in report.holdings if h.usd_value is None or h.usd_value >= args.min_usd
        ]

    if args.json:
        print(json.dumps(to_dict(report), indent=2))
    else:
        print(render_console(report, show_empty=args.all))

    if args.out:
        formats = [f for f in args.format.split(",") if f.strip()] or ["json"]
        written = export(report, args.out, formats, show_empty=args.all)
        for path in written:
            print(f"wrote {path}", file=sys.stderr)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    chains = load_chains(args.chains_file)
    drops = load_registry(args.registry, chains)
    results = verify_drops(drops, chains)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "id": r.drop_id,
                        "name": r.name,
                        "chain": r.chain,
                        "result": r.symbol,
                        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in r.checks],
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        return 0

    failures = 0
    for result in results:
        print(f"{result.symbol:<5} {result.name}  [{result.chain}]")
        for check in result.checks:
            state = "ok  " if check.ok is True else ("warn" if check.ok is None else "FAIL")
            print(f"  {state} {check.name}: {check.detail}")
        if result.symbol == "FAIL":
            failures += 1
    print()
    print(f"{len(results)} entr{'y' if len(results) == 1 else 'ies'}, {failures} failing")
    return 1 if failures else 0


def cmd_selftest(args: argparse.Namespace) -> int:
    report = run_selftest()
    if args.json:
        print(json.dumps(to_dict(report), indent=2))
    elif args.markdown:
        sys.stdout.write(render_markdown(report, show_empty=True))
        print()
        print(describe_invariants(report))
    else:
        print(render_console(report, show_empty=True))
        print()
        print(describe_invariants(report))
        print()
        print("This demo used a mock chain with zero network calls.")
    return 0


def cmd_dataset(args: argparse.Namespace) -> int:
    from .abi import calldata
    from .merkle import verify_proof
    from .verify import detect_proof_mode

    chains = load_chains(args.chains_file)
    chain = chains.get(args.chain)
    try:
        dataset = parse_dataset_file(args.path)
    except (OSError, DatasetError) as exc:
        print(f"cannot read {args.path}: {exc}", file=sys.stderr)
        return 2
    provenance = dataset_provenance(
        args.path,
        drop_id="",
        root=dataset.root,
        entry_count=len(dataset),
        tool_version=__version__,
    )
    root: bytes | None = None
    root_source = "argument"
    if args.root:
        root = bytes.fromhex(args.root.removeprefix("0x"))
    else:
        root_source = f"{args.root_method} on {chain.name}"
        rpc = Rpc(list(chain.rpcs))
        try:
            raw = rpc.eth_call(normalise_address(args.distributor), calldata(args.root_method))
            if isinstance(raw, str) and len(raw) >= 66:
                root = bytes.fromhex(raw[2:66])
        except Exception as exc:  # noqa: BLE001 - surfaced to the user below
            print(f"could not read {args.root_method}: {exc}", file=sys.stderr)

    checks: list[tuple[bool | None, str]] = []
    checks.append((len(dataset) > 0, f"{len(dataset)} claims parsed"))
    if dataset.root and root:
        label = "contract" if args.root is None else "reference root"
        checks.append(
            (
                dataset.root == root,
                f"file root matches {label}"
                if dataset.root == root
                else f"file root MISMATCHES {label}",
            )
        )
    elif dataset.root and not root:
        checks.append((None, "contract root unavailable"))
    elif root and not dataset.root:
        checks.append((None, "file declares no root"))

    mode = None
    verified = failed = 0
    if root:
        mode = detect_proof_mode(dataset, root)
        checks.append((True if mode else False, f"leaf encoding {mode[0]}{' double-hashed' if mode and mode[1] else ''}" if mode else "no leaf encoding reproduces the root"))
        if mode:
            items = list(dataset.claims.items())
            if not args.all_proofs:
                items = items[:25]
            for account, claim in items:
                if verify_proof(
                    claim.index, account, claim.amount, claim.proof, root,
                    encoding=mode[0], double_hash=mode[1], sort_pairs=dataset.sort_pairs,
                ):
                    verified += 1
                else:
                    failed += 1
            checks.append((failed == 0, f"{verified} proofs verified, {failed} failed"))

    if args.json:
        print(json.dumps({
            "path": str(args.path),
            "claims": len(dataset),
            "root": "0x" + root.hex() if root else None,
            "root_source": root_source,
            "encoding": mode[0] if mode else None,
            "proofs_verified": verified,
            "proofs_failed": failed,
            "checks": [{"ok": ok, "detail": detail} for ok, detail in checks],
            "provenance": provenance.as_dict(),
        }, indent=2))
    else:
        print(f"{args.path}: {len(dataset)} claims")
        print(f"sha256: {provenance.sha256}  ({provenance.byte_size:,} bytes)")
        if root:
            print(f"root ({root_source}): 0x{root.hex()}")
        for ok, detail in checks:
            state = "ok  " if ok is True else ("warn" if ok is None else "FAIL")
            print(f"  {state} {detail}")
    return 0 if all(ok is not False for ok, _ in checks) else 1


def cmd_chains(args: argparse.Namespace) -> int:
    chains = load_chains(args.chains_file)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "key": c.key,
                        "chain_id": c.chain_id,
                        "name": c.name,
                        "native": c.native.symbol,
                        "rpcs": list(c.rpcs),
                        "explorer": c.explorer,
                    }
                    for c in chains.chains.values()
                ],
                indent=2,
            )
        )
        return 0
    print(f"{'KEY':<12} {'ID':>8}  {'NATIVE':<6} {'NAME':<20} RPCS")
    for chain in sorted(chains.chains.values(), key=lambda c: c.chain_id):
        print(f"{chain.key:<12} {chain.chain_id:>8}  {chain.native.symbol:<6} {chain.name:<20} {len(chain.rpcs)}")
    return 0


def cmd_tokens(args: argparse.Namespace) -> int:
    chains = load_chains(args.chains_file)
    tokens = load_tokens(chains, args.tokens_file)
    keys = [args.chain.lower()] if args.chain else sorted(tokens)
    if args.json:
        payload = {
            key: [
                {"symbol": t.symbol, "address": t.address, "decimals": t.decimals}
                for t in tokens.get(key, [])
            ]
            for key in keys
        }
        print(json.dumps(payload, indent=2))
        return 0
    for key in keys:
        print(f"{key}:")
        for token in tokens.get(key, []):
            print(f"  {token.symbol:<8} {token.decimals:>2}d  {token.address}")
    return 0


def _settings(args: argparse.Namespace):
    return load_settings(getattr(args, "env_file", None))


def _service(args: argparse.Namespace):
    settings = _settings(args)
    override = getattr(args, "db", None)
    if override:
        settings = settings.with_overrides(db_path=override)
    return settings, PlatformService(settings)


def _money(value: float | None) -> str:
    return "unpriced" if value is None else f"${value:,.2f}"


def _write_binary_stdout(payload: bytes) -> None:
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
    else:  # tests and other text-only streams
        sys.stdout.write(payload.decode("utf-8", "replace"))


def cmd_scan_saved(args: argparse.Namespace) -> int:
    settings = _settings(args)
    override = getattr(args, "db", None)
    if override:
        settings = settings.with_overrides(db_path=override)
    service = PlatformService(settings, registry_path=args.registry)

    addresses = read_addresses(args.addresses, args.addresses_file)
    if args.saved:
        tracked = [wallet["address"] for wallet in service.db.list_wallets()]
        addresses = list(dict.fromkeys(tracked + addresses))
    if not addresses:
        print(
            "no addresses given\n\n"
            "  idlescan scan --save 0xabc...\n"
            "  idlescan scan --saved          # use `idlescan wallets add` addresses\n\n"
            "addresses are public; never paste a seed phrase or private key.",
            file=sys.stderr,
        )
        return 2
    if args.track:
        for address in addresses:
            service.add_wallet(address)

    chain_keys = [] if args.chains.strip().lower() in ("", "all", "*") else args.chains.split(",")
    try:
        snapshot_id, payload = service.run_scan_sync(chain_keys, trigger="cli", wallets=addresses)
    except ServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    totals = payload["totals"]
    print(f"snapshot #{snapshot_id} saved to {settings.db_path}")
    print(f"  wallets   {len(payload['wallets'])}")
    print(f"  idle      {_money(totals.get('idle_usd'))}")
    print(f"  unclaimed {_money(totals.get('unclaimed_usd'))}")
    print(f"  claims    {len(payload['claims'])} positions")
    print(f"  alerts    {len(service.alerts())} open")
    if payload.get("warnings"):
        for warning in payload["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
    print("\nnext: idlescan claims list")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .api import run

    settings = _settings(args)
    settings = settings.with_overrides(
        host=args.host,
        port=args.port,
        db_path=args.db,
        demo=True if args.demo else None,
    )
    try:
        return run(settings)
    except Exception as exc:  # noqa: BLE001 - friendly startup failure
        print(f"error: {exc}", file=sys.stderr)
        return 2


def cmd_wallets(args: argparse.Namespace) -> int:
    _, service = _service(args)
    command = args.wallet_command
    if command == "add":
        try:
            wallet = service.add_wallet(args.address, args.label, args.tags)
        except ServiceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(wallet, indent=2))
        else:
            print(f"tracking {wallet['address']}")
            if wallet.get("label"):
                print(f"  label {wallet['label']}")
        return 0
    if command == "list":
        wallets = service.list_wallets()
        if args.json:
            print(json.dumps(wallets, indent=2))
            return 0
        if not wallets:
            print("no wallets tracked; add one with `idlescan wallets add 0x...`")
            return 0
        print(f"{'LABEL':<20} {'ADDRESS':<44} {'IDLE':>12} {'UNCLAIMED':>12} TAGS")
        for wallet in wallets:
            print(
                f"{(wallet['label'] or '-')[:20]:<20} {wallet['address']:<44} "
                f"{_money(wallet.get('idle_usd')):>12} {_money(wallet.get('unclaimed_usd')):>12} "
                f"{wallet.get('tags') or ''}"
            )
        return 0
    if command == "rm":
        if not service.delete_wallet(args.address):
            print(f"error: {args.address} is not tracked", file=sys.stderr)
            return 1
        print(f"stopped tracking {args.address.lower()}")
        return 0
    print(f"unknown wallets command {command}", file=sys.stderr)
    return 2


def cmd_check(args: argparse.Namespace) -> int:
    try:
        _, service = _service(args)
        result = service.check_address(args.address)
    except ServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    totals = result["totals"]
    claims = result["claims"]
    holdings = result["holdings"]
    print(f"address    {result['address']}")
    print(
        f"mode       {'demo ledger' if result['demo'] else 'live scan'} · "
        f"{'tracked' if result['tracked'] else 'not tracked'} · checked {result['checked_at']}"
    )
    print(f"idle       {_money(totals['idle_usd'])} across {totals['assets']} asset(s)")
    print(
        f"unclaimed  {_money(totals['unclaimed_usd'])} gross · {_money(totals['net_usd'])} net "
        f"after {_money(totals['gas_usd'])} gas · {totals['unclaimed']} position(s)"
    )
    if totals["claimed"]:
        print(f"claimed    {_money(totals['claimed_usd'])} across {totals['claimed']} position(s)")
    print("nothing was saved: no watchlist entry, snapshot or alert")

    if claims:
        print()
        print(f"{'DROP':<22} {'STATUS':<12} {'VALUE':>10} {'GAS':>8} {'NET':>10} {'DEADLINE':<12} PROOF")
        for row in claims:
            proof = {1: "verified", 0: "MISMATCH"}.get(row["proof_verified"], "unverified")
            status = "expired" if row["expired"] else row["status"]
            deadline = (row["deadline"] or "-")[:10]
            print(
                f"{row['drop_id'][:22]:<22} {status:<12} {_money(row['usd_value']):>10} "
                f"{_money(row['gas_usd']):>8} {_money(row['net_usd']):>10} {deadline:<12} {proof}"
            )

    if holdings:
        print()
        print(f"{'ASSET':<10} {'CHAIN':<12} {'AMOUNT':>18} {'VALUE':>11}")
        for row in sorted(holdings, key=lambda item: item["usd_value"] or 0, reverse=True):
            print(
                f"{str(row['asset'])[:10]:<10} {str(row['chain'])[:12]:<12} "
                f"{str(row.get('amount', row['raw']))[:18]:>18} {_money(row['usd_value']):>11}"
            )

    for warning in result.get("warnings") or []:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def cmd_claims(args: argparse.Namespace) -> int:
    _, service = _service(args)
    command = args.claim_command
    if command == "list":
        snapshot = service.db.latest_snapshot()
        if not snapshot:
            print("no snapshot yet; run `idlescan scan --save` or start `idlescan serve`", file=sys.stderr)
            return 1
        rows = service.db.claims(snapshot["id"], wallet=args.wallet, min_net=args.min_net)
        if not args.all:
            rows = [row for row in rows if row["status"] == "unclaimed" and not row["expired"]]
        if args.json:
            print(json.dumps({"snapshot": snapshot["id"], "claims": rows}, indent=2))
            return 0
        if not rows:
            print("no claim positions match")
            return 0
        print(f"snapshot #{snapshot['id']} · {snapshot['created_at']}")
        print(f"{'DROP':<18} {'WALLET':<14} {'STATUS':<13} {'VALUE':>11} {'GAS':>9} {'NET':>11} {'DEADLINE':<12} PROOF")
        for row in rows:
            proof = {1: "verified", 0: "MISMATCH"}.get(row["proof_verified"], "unverified")
            deadline = (row["deadline"] or "-")[:10]
            print(
                f"{row['drop_id'][:18]:<18} {row['wallet'][:12]:<14} {row['status']:<13} "
                f"{_money(row['usd_value']):>11} {_money(row['gas_usd']):>9} "
                f"{_money(row['net_usd']):>11} {deadline:<12} {proof}"
            )
        claimed = sum(1 for row in rows if row["status"] == "claimed")
        print(f"\n{len(rows)} position(s), {claimed} claimed, {len(rows) - claimed} open")
        return 0
    if command == "prepare":
        try:
            result = service.prepare_claim(args.drop_id, args.wallet)
        except ServiceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        claim = result["claim"]
        tx = result["transaction"]
        print(f"unsigned claim for {claim['name']} on {claim['chain']}")
        print(f"  amount   {claim['amount']} raw (decimals {claim['decimals']})")
        print(f"  value    {_money(claim['usd_value'])} gross · {_money(claim['gas_usd'])} gas · {_money(claim['net_usd'])} net")
        print(f"  from     {tx['from']}")
        print(f"  to       {tx['to']}")
        print(f"  chainId  {tx['chainId']}")
        print(f"  data     {tx['data']}")
        print("\nthis server does not sign or broadcast. Paste the transaction into your wallet,")
        print("verify the destination against the project's official docs, then sign there.")
        for warning in result.get("warnings", []):
            print(f"warning: {warning}", file=sys.stderr)
        return 0
    if command == "confirm":
        try:
            result = service.confirm_claim(
                drop_id=args.drop_id, wallet=args.wallet, tx_hash=args.tx_hash
            )
        except ServiceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['status']}: {result['tx_hash']} ({result['detail']})")
        return 0
    print(f"unknown claims command {command}", file=sys.stderr)
    return 2


def cmd_alerts(args: argparse.Namespace) -> int:
    _, service = _service(args)
    if args.ack_all:
        count = service.ack_all_alerts()
        print(f"acknowledged {count} alert(s)")
        return 0
    if args.ack is not None:
        if not service.ack_alert(args.ack):
            print(f"error: alert {args.ack} not found", file=sys.stderr)
            return 1
        print(f"acknowledged alert {args.ack}")
        return 0
    alerts = service.alerts(include_ack=args.all)
    if args.json:
        print(json.dumps(alerts, indent=2))
        return 0
    if not alerts:
        print("no open alerts")
        return 0
    for alert in alerts:
        print(f"[{alert['severity']:<8}] {alert['title']}")
        print(f"  {alert['body']}")
        print(f"  {alert['kind']} · {alert['created_at']}" + (f" · {alert['wallet']}" if alert["wallet"] else ""))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    _, service = _service(args)
    try:
        filename, _content_type, payload = service.export(args.format)
    except ServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    target = args.out
    if target:
        if target.is_dir():
            target = target / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        print(f"wrote {target}", file=sys.stderr)
    else:
        _write_binary_stdout(payload)
    return 0


def cmd_calendar(args: argparse.Namespace) -> int:
    _, service = _service(args)
    payload = service.calendar_ics(args.drop)
    target = args.out
    if target:
        if target.is_dir():
            target = target / "idlescan-deadlines.ics"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        print(f"wrote {target}", file=sys.stderr)
    else:
        _write_binary_stdout(payload)
    return 0


def _db_path(args: argparse.Namespace) -> Path:
    settings = _settings(args)
    override = getattr(args, "db", None)
    if override:
        settings = settings.with_overrides(db_path=override)
    return settings.db_path


def cmd_db(args: argparse.Namespace) -> int:
    settings = _settings(args)
    override = getattr(args, "db", None)
    if override:
        settings = settings.with_overrides(db_path=override)

    if args.db_command == "status":
        payload = Database(settings.db_path).status()
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
            return 0
        print(f"path            {payload['path']}")
        print(f"exists          {'yes' if payload['exists'] else 'no'}")
        print(f"size            {payload['size_bytes']:,} bytes")
        print(
            f"schema          v{payload['schema_version']} "
            f"(latest v{payload['latest_schema_version']})"
            + ("" if payload["up_to_date"] else f"  ·  {payload['pending_migrations']} pending")
        )
        for table, count in payload["counts"].items():
            print(f"{table:<15} {count:,}")
        if payload["latest_snapshot"]:
            latest = payload["latest_snapshot"]
            print(f"latest snapshot #{latest['id']}  {latest['created_at']}  ({latest['trigger']})")
        return 0

    if args.db_command == "migrate":
        db = Database(settings.db_path)
        before = db.status()
        db.init()
        after = db.status()
        backup = None
        if before["schema_version"]:
            candidate = settings.db_path.with_name(
                f"{settings.db_path.name}.pre-v{before['schema_version']}.bak"
            )
            if candidate.is_file():
                backup = str(candidate)
        payload = {
            "path": str(settings.db_path),
            "from_version": before["schema_version"],
            "to_version": after["schema_version"],
            "applied": getattr(db, "applied_migrations", []),
            "backup": backup,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        if payload["applied"]:
            print(
                f"migrated {payload['path']} from v{payload['from_version']} "
                f"to v{payload['to_version']} ({payload['applied']})"
            )
            if backup:
                print(f"pre-migration backup: {backup}")
        else:
            print(f"{payload['path']} is already at schema v{payload['to_version']}")
        return 0

    if args.db_command == "check":
        payload = Database(settings.db_path).check()
        if args.json:
            print(json.dumps(payload, indent=2))
        elif payload["ok"]:
            print(
                f"ok: {payload['checks']['counts']} "
                f"schema v{payload['schema_version']}"
            )
        else:
            print("database check found problems:", file=sys.stderr)
            for problem in payload["problems"]:
                print(f"  - {problem}", file=sys.stderr)
        return 0 if payload["ok"] else 1

    if args.db_command == "prune":
        def choose(value, setting):
            return setting if value is None else value

        db = Database(settings.db_path).init()
        payload = db.prune(
            snapshots=choose(args.snapshots, settings.retention_snapshots),
            alerts_days=choose(args.alerts_days, settings.retention_alerts_days),
            audit_days=choose(args.audit_days, settings.retention_audit_days),
            executions_days=choose(args.executions_days, settings.retention_executions_days),
            jobs_days=choose(args.jobs_days, settings.retention_jobs_days),
            dry_run=not args.yes,
            vacuum=args.vacuum,
        )
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        if not payload["deleted"]:
            print("nothing to prune")
            return 0
        verb = "would delete" if payload["dry_run"] else "deleted"
        for table, count in payload["deleted"].items():
            print(f"{verb:<13} {count:>6} {table}")
        if payload["dry_run"]:
            print("dry run: re-run with --yes to apply")
        elif payload["vacuumed"]:
            print("vacuumed")
        return 0

    raise SystemExit(f"unknown db command {args.db_command!r}")


def cmd_backup(args: argparse.Namespace) -> int:
    _, service = _service(args)
    try:
        path = service.backup(args.out)
    except (OSError, ServiceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"path": str(path), "size": path.stat().st_size}, indent=2))
    else:
        print(f"backup written to {path} ({path.stat().st_size:,} bytes)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "scan": cmd_scan,
        "verify": cmd_verify,
        "selftest": cmd_selftest,
        "dataset": cmd_dataset,
        "chains": cmd_chains,
        "tokens": cmd_tokens,
        "serve": cmd_serve,
        "wallets": cmd_wallets,
        "claims": cmd_claims,
        "check": cmd_check,
        "alerts": cmd_alerts,
        "export": cmd_export,
        "backup": cmd_backup,
        "db": cmd_db,
        "calendar": cmd_calendar,
    }
    try:
        return handlers[args.command](args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except SettingsError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
