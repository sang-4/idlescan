"""Rendering: terminal tables plus JSON / CSV / Markdown exports."""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence

from .gas import GAS_ESTIMATE_NOTE
from .scanner import (
    CLAIMED,
    ERROR,
    NO_ALLOCATION,
    NO_SOURCE,
    UNCLAIMED,
    UNKNOWN,
    ClaimCheck,
    ScanReport,
)

DISCLAIMER = "Research tool, not financial advice. Verify contract addresses against official sources."

STATUS_ORDER = {UNCLAIMED: 0, CLAIMED: 1, UNKNOWN: 2, ERROR: 3, NO_SOURCE: 4, NO_ALLOCATION: 5}


def fmt_amount(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        return "0"
    if value < Decimal("0.000001"):
        return f"{value:.2e}"
    return text


def fmt_gas(claim: "ClaimCheck") -> str:
    if claim.gas_usd is not None:
        return fmt_usd(claim.gas_usd)
    if claim.gas_native is not None:
        return f"{claim.gas_native:.6f} {claim.native_symbol}"
    return "-"


def fmt_usd(value: float | None) -> str:
    if value is None:
        return "-"
    if value and abs(value) < 0.01:
        return "<$0.01"
    return f"${value:,.2f}"


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]], aligns: Sequence[str] | None = None) -> str:
    rows = [list(map(str, row)) for row in rows]
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    aligns = aligns or ["<"] * len(headers)

    def line(cells: Sequence[str]) -> str:
        return "  ".join(f"{cell:{aligns[i]}{widths[i]}}" for i, cell in enumerate(cells)).rstrip()

    out = [line(headers), "  ".join("-" * w for w in widths)]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def visible_claims(report: ScanReport, *, show_empty: bool = False) -> list[ClaimCheck]:
    claims = [c for c in report.claims if show_empty or c.status != NO_ALLOCATION]
    return sorted(claims, key=lambda c: (STATUS_ORDER.get(c.status, 9), -(c.usd_value or 0.0), c.name))


def render_console(report: ScanReport, *, show_empty: bool = False, show_claimed: bool = True) -> str:
    blocks: list[str] = []
    header = f"idlescan · {len(report.wallets)} wallet(s) · {len(report.chains)} chain(s) · {report.generated_at}"
    blocks.append(header)

    holdings = sorted(report.holdings, key=lambda h: (-(h.usd_value or 0.0), h.chain, h.symbol))
    if holdings:
        rows = [
            [
                h.chain,
                h.symbol,
                fmt_amount(h.amount),
                fmt_usd(h.usd_value),
                _short_wallet(h.wallet),
            ]
            for h in holdings
        ]
        blocks.append(
            "IDLE BALANCES\n" + _table(["CHAIN", "ASSET", "AMOUNT", "USD", "WALLET"], rows, ["<", "<", ">", ">", "<"])
        )
    else:
        blocks.append("IDLE BALANCES\n(none found on the scanned chains)")

    claims = visible_claims(report, show_empty=show_empty)
    if not show_claimed:
        claims = [c for c in claims if c.status != CLAIMED]
    if claims:
        rows = []
        for claim in claims:
            rows.append([
                _status_label(claim),
                claim.chain,
                claim.name,
                fmt_amount(claim.amount) if claim.amount_raw else "-",
                "-" if claim.status == CLAIMED or not claim.amount_raw else fmt_usd(claim.usd_value),
                fmt_gas(claim) if claim.status == UNCLAIMED else "-",
                fmt_usd(claim.net_usd) if claim.net_usd is not None else "-",
                _short_wallet(claim.wallet),
            ])
        blocks.append(
            "AIRDROPS\n"
            + _table(
                ["STATUS", "CHAIN", "DROP", "AMOUNT", "VALUE", "GAS", "NET", "WALLET"],
                rows,
                ["<", "<", "<", ">", ">", ">", ">", "<"],
            )
        )
    else:
        blocks.append("AIRDROPS\n(no claim positions matched)")

    hidden = len([c for c in report.claims if c.status == NO_ALLOCATION])
    footnotes: list[str] = []
    if hidden and not show_empty:
        footnotes.append(f"{hidden} address/drop combination(s) had no allocation (use --all to list them).")
    notes = [c for c in report.claims if c.note]
    for note in notes[:6]:
        footnotes.append(f"note: {note.name} / {_short_wallet(note.wallet)}: {note.note}")
    if report.price_error:
        footnotes.append(f"prices: {report.price_error}")
    for warning in report.warnings[:12]:
        footnotes.append(f"warning: {warning}")
    if len(report.warnings) > 12:
        footnotes.append(f"... {len(report.warnings) - 12} more warning(s) not shown")
    if footnotes:
        blocks.append("NOTES\n" + "\n".join(f"  {line}" for line in footnotes))

    blocks.append(
        "TOTALS\n"
        f"  idle balances   {fmt_usd(report.idle_value)}\n"
        f"  unclaimed       {fmt_usd(report.claim_value)}\n"
        f"  combined        {fmt_usd(report.total_value)}"
    )
    blocks.append("GAS ESTIMATE\n  " + GAS_ESTIMATE_NOTE)
    blocks.append("DISCLAIMER\n  " + DISCLAIMER)
    return "\n\n".join(blocks)


def _status_label(claim: ClaimCheck) -> str:
    if claim.status == UNCLAIMED and claim.expired:
        return "expired"
    return claim.status


def _short_wallet(wallet: str) -> str:
    return f"{wallet[:6]}…{wallet[-4:]}"


def to_dict(report: ScanReport) -> dict:
    return {
        "generated_at": report.generated_at,
        "wallets": report.wallets,
        "chains": report.chains,
        "blocks": report.block_numbers,
        "totals": {
            "idle_usd": report.idle_value,
            "unclaimed_usd": report.claim_value,
            "combined_usd": report.total_value,
        },
        "holdings": [
            {
                "chain": h.chain,
                "chain_id": h.chain_id,
                "wallet": h.wallet,
                "asset": h.symbol,
                "asset_address": h.asset_address,
                "decimals": h.decimals,
                "raw": str(h.raw),
                "amount": str(h.amount),
                "usd_price": h.usd_price,
                "usd_value": h.usd_value,
            }
            for h in report.holdings
        ],
        "claims": [
            {
                "drop_id": c.drop_id,
                "name": c.name,
                "chain": c.chain,
                "chain_id": c.chain_id,
                "wallet": c.wallet,
                "distributor": c.distributor,
                "token": c.token,
                "status": c.status,
                "expired": c.expired,
                "deadline": c.deadline,
                "amount_raw": str(c.amount_raw),
                "amount": str(c.amount),
                "usd_price": c.usd_price,
                "usd_value": c.usd_value,
                "gas_native": c.gas_native,
                "gas_usd": c.gas_usd,
                "gas_cost_native_buffered": c.gas_cost_native_buffered,
                "gas_cost_usd_buffered": c.gas_cost_usd_buffered,
                "gas_units": c.gas_units,
                "gas_limit": c.gas_limit,
                "fee_per_gas": c.fee_per_gas,
                "max_fee_per_gas": c.max_fee_per_gas,
                "max_priority_fee_per_gas": c.max_priority_fee_per_gas,
                "fee_model": c.fee_model,
                "fee_source": c.fee_source,
                "l1_fee_wei": str(c.l1_fee_wei) if c.l1_fee_wei is not None else None,
                "gas_estimate_quality": c.gas_estimate_quality,
                "gas_note": c.gas_note,
                "gas_buffer_pct": c.gas_buffer_pct,
                "net_usd": c.net_usd,
                "net_usd_raw": c.net_usd_raw,
                "net_group": c.net_group,
                "proof_verified": c.proof_verified,
                "root": c.root,
                "eligibility_source": c.source,
                "note": c.note,
                "claim_calldata": c.calldata,
            }
            for c in report.claims
        ],
        "gas_estimate_note": GAS_ESTIMATE_NOTE,
        "warnings": report.warnings,
        "price_error": report.price_error,
        "datasets": report.datasets,
    }


def write_json(report: ScanReport, path: Path) -> None:
    path.write_text(json.dumps(to_dict(report), indent=2) + "\n")


def write_csv(report: ScanReport, path: Path) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "kind",
        "chain",
        "wallet",
        "asset_or_drop",
        "status",
        "amount",
        "raw",
        "decimals",
        "usd_price",
        "usd_value",
        "gas_usd",
        "net_usd",
        "note",
        "reference",
        "gas_units",
        "fee_per_gas",
        "l1_fee_wei",
        "gas_estimate_quality",
        "gas_usd_buffered",
        "net_usd_raw",
    ])
    for holding in report.holdings:
        writer.writerow([
            "balance" if holding.is_native else "token",
            holding.chain,
            holding.wallet,
            holding.symbol,
            "",
            str(holding.amount),
            str(holding.raw),
            holding.decimals,
            holding.usd_price if holding.usd_price is not None else "",
            holding.usd_value if holding.usd_value is not None else "",
            "",
            "",
            "",
            holding.asset_address or "",
            "",
            "",
            "",
            "",
            "",
            "",
        ])
    for claim in report.claims:
        writer.writerow([
            "claim",
            claim.chain,
            claim.wallet,
            claim.name,
            _status_label(claim),
            str(claim.amount) if claim.amount_raw else "",
            str(claim.amount_raw) if claim.amount_raw else "",
            claim.decimals,
            claim.usd_price if claim.usd_price is not None else "",
            claim.usd_value if claim.usd_value is not None else "",
            claim.gas_usd if claim.gas_usd is not None else "",
            claim.net_usd if claim.net_usd is not None else "",
            claim.note or "",
            claim.distributor,
            claim.gas_units if claim.gas_units is not None else "",
            claim.fee_per_gas if claim.fee_per_gas is not None else "",
            claim.l1_fee_wei if claim.l1_fee_wei is not None else "",
            claim.gas_estimate_quality,
            claim.gas_cost_usd_buffered if claim.gas_cost_usd_buffered is not None else "",
            claim.net_usd_raw if claim.net_usd_raw is not None else "",
        ])
    # Report-level metadata is emitted as one trailer row with kind="note"
    # so the header stays first and row-oriented consumers keep working.
    writer.writerow([
        "note",
        "",
        "",
        "gas_estimate_note",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        GAS_ESTIMATE_NOTE,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ])
    path.write_text(buffer.getvalue())


def render_markdown(report: ScanReport, *, show_empty: bool = False) -> str:
    lines = [
        "# Idle asset & unclaimed airdrop report",
        "",
        f"- Generated: {report.generated_at}",
        f"- Wallets: {', '.join(report.wallets)}",
        f"- Chains: {', '.join(report.chains)}",
        "",
        "## Totals",
        "",
        "| Metric | USD |",
        "| --- | ---: |",
        f"| Idle balances | {fmt_usd(report.idle_value)} |",
        f"| Unclaimed airdrops | {fmt_usd(report.claim_value)} |",
        f"| Combined | {fmt_usd(report.total_value)} |",
        "",
    ]
    holdings = sorted(report.holdings, key=lambda h: -(h.usd_value or 0.0))
    lines += ["## Idle balances", ""]
    if holdings:
        lines += ["| Chain | Asset | Amount | USD | Wallet |", "| --- | --- | ---: | ---: | --- |"]
        for h in holdings:
            lines.append(
                f"| {h.chain} | {h.symbol} | {fmt_amount(h.amount)} | {fmt_usd(h.usd_value)} | `{h.wallet}` |"
            )
    else:
        lines.append("_No balances found._")

    claims = visible_claims(report, show_empty=show_empty)
    lines += ["", "## Airdrops", ""]
    if claims:
        lines += [
            "| Status | Chain | Drop | Amount | Value | Gas | Gas quality | Net | Proof | Wallet |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- | --- |",
        ]
        for c in claims:
            proof = {True: "verified", False: "**INVALID**", None: "unverified"}[c.proof_verified]
            lines.append(
                f"| {_status_label(c)} | {c.chain} | {c.name} | "
                f"{fmt_amount(c.amount) if c.amount_raw else '-'} | "
                f"{'-' if c.status == CLAIMED or not c.amount_raw else fmt_usd(c.usd_value)} | "
                f"{fmt_gas(c) if c.status == UNCLAIMED else '-'} | "
                f"{c.gas_estimate_quality if c.status == UNCLAIMED else '-'} | "
                f"{fmt_usd(c.net_usd) if c.net_usd is not None else '-'} | {proof} | `{c.wallet}` |"
            )
    else:
        lines.append("_No claim positions matched._")

    notes = [c.note for c in report.claims if c.note]
    if notes or report.warnings:
        lines += ["", "## Notes", ""]
        for note in notes:
            lines.append(f"- {note}")
        for warning in report.warnings:
            lines.append(f"- warning: {warning}")
    if report.price_error:
        lines += ["", f"> Prices unavailable: {report.price_error}"]
    lines += ["", f"> **Gas estimate:** {GAS_ESTIMATE_NOTE}"]
    lines += ["", DISCLAIMER]
    return "\n".join(lines) + "\n"


def export(report: ScanReport, path: Path | str, formats: Sequence[str], *, show_empty: bool = False) -> list[Path]:
    base = Path(path)
    written: list[Path] = []
    for fmt in formats:
        fmt = fmt.lower().strip()
        if not fmt:
            continue
        target = base if len(formats) == 1 and base.suffix.lower() == f".{fmt}" else base.with_suffix(f".{fmt}")
        if fmt == "json":
            write_json(report, target)
        elif fmt == "csv":
            write_csv(report, target)
        elif fmt == "md":
            target.write_text(render_markdown(report, show_empty=show_empty))
        else:
            raise ValueError(f"unknown export format {fmt!r} (use json, csv or md)")
        written.append(target)
    return written
