"""Application service: wallets, scans, snapshots, alerts, claim preparation.

This is the layer the HTTP API and the CLI both call, so behaviour (including the
read-only gate) is identical no matter how the product is driven.
"""

from __future__ import annotations

import io
import json
import math
import re
import sqlite3
import threading
import uuid
from concurrent.futures import CancelledError
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

from .abi import normalise_address
from .cache import Cache
from .chains import ChainConfig, Token, load_chains, load_tokens
from .config import PROJECT_ROOT, Settings
from .db import Database
from .notifications import Notifier
from .preflight import PreflightError, run_preflight
from .prices import PriceOracle
from .redaction import redact_text
from .registry import Airdrop, ConfigError, load_registry, parse_registry
from .report import render_markdown, to_dict
from .rpc import EndpointHealth, Rpc
from .safe_http import safe_fetch_json
from .scanner import CLAIMED, ERROR, NO_ALLOCATION, UNCLAIMED, Scanner, ScanReport

TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
# Alerts that describe a condition that can become false again.  Event alerts
# (new allocation, claim confirmed) are deliberately excluded from the sweep.
CONDITION_ALERT_KINDS = (
    "below_gas",
    "deadline",
    "invalid_proof",
    "claim_error",
    "missing_dataset",
    "missing_eligibility",
)
# Absolute filesystem paths (POSIX or Windows) in text that is shown in the UI.
_ABS_PATH = re.compile(r"(?<![:/\w])(?:[A-Za-z]:\\|/)(?:[^\s/\\'\"]+[\\/])+([^\s/\\'\"]+)")


def public_text(value: str) -> str:
    """Reduce absolute filesystem paths to their filename.

    Alerts are persisted, so an older row can still contain a path from before
    paths were sanitised at generation time. Scrubbing on read keeps every
    user-facing surface free of host-specific locations.
    """
    scrubbed = _ABS_PATH.sub(lambda match: match.group(1), str(value))
    return redact_text(scrubbed)


class ServiceError(RuntimeError):
    status = 400


class NotFound(ServiceError):
    status = 404


class WriteDisabled(ServiceError):
    status = 403


class Conflict(ServiceError):
    status = 409


class Lockdown(ServiceError):
    """Local mutations are disabled by IDLESCAN_LOCKDOWN."""

    status = 403


@dataclass
class ScanRequest:
    wallets: list[str]
    chains: list[str]
    trigger: str = "manual"
    cancel_event: threading.Event | None = None


class PlatformService:
    def __init__(
        self,
        settings: Settings,
        *,
        rpc_factory: Callable | None = None,
        scanner_factory: Callable | None = None,
        registry_path: str | Path | None = None,
    ):
        self.settings = settings
        self.rpc_factory = rpc_factory
        self.scanner_factory = scanner_factory
        self.registry_path = registry_path
        self.db = Database(settings.db_path).init()
        self.endpoint_health = EndpointHealth()
        self.notifier = Notifier.from_settings(settings, db=self.db)
        self._jobs: dict[str, threading.Thread] = {}
        self._job_lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._active_scan: str | None = None
        self._cancel_events: dict[str, threading.Event] = {}
        self.chains: ChainConfig = load_chains()
        self.tokens: dict[str, list[Token]] = load_tokens(self.chains)
        self.seed_registry()
        self._recover_stale_jobs()

    # ------------------------------------------------------------------ setup
    def _recover_stale_jobs(self) -> None:
        """Mark jobs left ``running`` after a restart as interrupted."""
        with self.db.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT id FROM jobs WHERE status = 'running'"
                ).fetchall()
            ]
        for row in rows:
            self.db.update_job(
                row["id"],
                status="failed",
                message="scan interrupted by restart",
                error="interrupted",
                finished_at=_now(),
            )
            self.db.log("scanner", "scan.recovered", f"job={row['id']}")

    # ------------------------------------------------------------------ scans
    def seed_registry(self) -> None:
        """Copy bundled registry entries into the DB without clobbering local edits."""
        try:
            drops = load_registry(self.registry_path, self.chains)
        except ConfigError:
            return
        for drop in drops:
            if drop.eligibility and drop.eligibility.dataset and self.settings.dataset_dir:
                current = Path(drop.eligibility.dataset)
                if not current.exists():
                    candidate = Path(self.settings.dataset_dir).expanduser() / current.name
                    if candidate.exists():
                        drop.eligibility.dataset = str(candidate.resolve())
            existing = self.db.get_airdrop(drop.id)
            enabled = bool(existing["enabled"]) if existing else drop.enabled
            self.db.upsert_airdrop(
                drop.id, _drop_payload(drop), enabled=enabled, source="registry"
            )

    def registry_drops(self) -> list[Airdrop]:
        """Enabled drops, parsed back from the DB payloads."""
        rows = self.db.list_airdrops(enabled_only=True)
        payload = {"airdrops": [row["payload"] for row in rows]}
        try:
            # Claim preparation works from a snapshot; the dataset file is only
            # needed to *discover* positions, not to hand off one that exists.
            return parse_registry(payload, self.chains, require_dataset=False)
        except ConfigError:
            return []

    # --------------------------------------------------------------- gating
    def _require_local_edits(self, action: str) -> None:
        if self.settings.lockdown:
            raise Lockdown(
                f"local edits are locked (IDLESCAN_LOCKDOWN=true); cannot {action}. "
                "Reads, exports and POST /api/check are still available."
            )

    # ---------------------------------------------------------------- wallets
    def add_wallet(self, address: str, label: str = "", tags: str = "") -> dict:
        self._require_local_edits("add a wallet")
        try:
            normalised = normalise_address(address)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        wallet = self.db.add_wallet(normalised, label.strip()[:60], tags.strip()[:120])
        self.db.log("api", "wallet.add", normalised)
        return wallet

    def list_wallets(self) -> list[dict]:
        wallets = self.db.list_wallets()
        latest = self.db.latest_snapshot()
        if not latest:
            return wallets
        holdings = self.db.holdings(latest["id"])
        claims = self.db.claims(latest["id"])
        for wallet in wallets:
            wallet_holdings = [h for h in holdings if h["wallet"] == wallet["address"]]
            wallet_claims = [c for c in claims if c["wallet"] == wallet["address"]]
            wallet["idle_usd"] = sum(h["usd_value"] or 0 for h in wallet_holdings)
            wallet["assets"] = len(wallet_holdings)
            wallet["unclaimed_usd"] = sum(
                c["usd_value"] or 0
                for c in wallet_claims
                if c["status"] == UNCLAIMED and not c["expired"]
            )
            wallet["unclaimed"] = sum(
                1 for c in wallet_claims if c["status"] == UNCLAIMED and not c["expired"]
            )
            wallet["claimable_net_usd"] = sum(
                c["net_usd"] or 0
                for c in wallet_claims
                if c["status"] == UNCLAIMED and not c["expired"]
            )
        return wallets

    def delete_wallet(self, address: str) -> bool:
        self._require_local_edits("delete a wallet")
        removed = self.db.remove_wallet(address)
        if removed:
            self.db.log("api", "wallet.remove", address.lower())
        return removed

    # ------------------------------------------------------------------ scans
    def start_scan(self, chain_keys: Sequence[str] | None = None, trigger: str = "manual") -> dict:
        self._require_local_edits("start a scan")
        wallets = [w["address"] for w in self.db.list_wallets()]
        if not wallets:
            raise ServiceError("add at least one wallet before scanning")
        chains = [c for c in (chain_keys or self.settings.chains or ["all"]) if c]
        cancel_event = threading.Event()
        with self._job_lock:
            if self._active_scan:
                raise Conflict("a scan is already running")
            job_id = uuid.uuid4().hex[:12]
            self.db.create_job(job_id, "scan")
            self._cancel_events[job_id] = cancel_event
            thread = threading.Thread(
                target=self._run_scan,
                args=(job_id, ScanRequest(wallets=wallets, chains=chains, trigger=trigger, cancel_event=cancel_event)),
                name=f"scan-{job_id}",
                daemon=True,
            )
            self._jobs[job_id] = thread
            self._active_scan = job_id
            thread.start()
        return self.db.get_job(job_id)

    def cancel_scan(self, job_id: str) -> bool:
        """Request cancellation of a running scan.

        Returns ``True`` if the scan was found and cancellation requested,
        ``False`` if the job does not exist or is not running.
        """
        with self._job_lock:
            if job_id not in self._cancel_events:
                return False
            event = self._cancel_events[job_id]
        event.set()
        self.db.log("scanner", "scan.cancel", f"job={job_id}")
        return True

    def _run_scan(self, job_id: str, request: ScanRequest) -> None:
        cancel_event = request.cancel_event or threading.Event()
        messages: list[str] = []

        def progress(message: str) -> None:
            messages.append(message)
            self.db.update_job(job_id, status="running", message=message, progress=min(0.9, 0.1 + 0.05 * len(messages)))

        try:
            self.db.update_job(job_id, status="running", message="building scanner")
            if self.settings.demo:
                progress("running offline demo scan")
                report = self._demo_report()
            else:
                report = self.build_scanner(progress).scan(request.chains, request.wallets)
            if cancel_event.is_set():
                raise CancelledError("scan cancelled")
            _apply_min_usd(report, self.settings.min_usd)
            payload = to_dict(report)
            snapshot_id = self.db.save_snapshot(
                chains=report.chains,
                wallets=report.wallets,
                totals=payload["totals"],
                report=payload,
                trigger=request.trigger,
            )
            alerts = self._generate_alerts(snapshot_id, payload)
            self._persist_datasets(payload)
            result = {
                "snapshot_id": snapshot_id,
                "totals": payload["totals"],
                "holdings": len(payload["holdings"]),
                "claims": len(payload["claims"]),
                "alerts": alerts,
            }
            if report.phase_timings:
                result["phase_timings"] = report.phase_timings
            self.db.update_job(
                job_id,
                status="done",
                progress=1.0,
                message=f"snapshot #{snapshot_id} saved",
                finished_at=_now(),
                result=result,
            )
            self.db.log("scanner", "scan.complete", f"snapshot={snapshot_id} alerts={alerts}")
        except CancelledError:
            self.db.update_job(
                job_id,
                status="failed",
                message="scan cancelled",
                error="cancelled",
                finished_at=_now(),
            )
            self.db.log("scanner", "scan.cancelled", f"job={job_id}")
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self.db.update_job(
                job_id,
                status="failed",
                message="scan failed",
                error=f"{exc.__class__.__name__}: {exc}",
                finished_at=_now(),
            )
            self.db.log("scanner", "scan.failed", str(exc)[:300])
        finally:
            with self._job_lock:
                self._active_scan = None
                self._cancel_events.pop(job_id, None)

    def build_scanner(self, progress: Callable[[str], None] | None = None) -> Scanner:
        if self.scanner_factory:
            return self.scanner_factory(self)
        return Scanner(
            self.chains,
            self.tokens,
            self.registry_drops(),
            cache=Cache(),
            prices=PriceOracle(),
            rpc_factory=self.rpc_factory or (lambda chain: self._new_rpc(chain)),
            verbose=progress,
            gas_buffer_pct=self.settings.gas_buffer_pct,
            dataset_stale_days=self.settings.dataset_stale_days,
            scan_workers=self.settings.scan_workers,
            timeout_seconds=self.settings.scan_timeout_seconds,
            http_fetch=lambda url: safe_fetch_json(
                url,
                allow_http=self.settings.allow_http_fetch,
                max_bytes=self.settings.max_fetch_bytes,
                allowed_hosts=self.settings.fetch_allowed_hosts,
                timeout=self.settings.fetch_timeout_seconds,
            ),
        )

    def run_scan_sync(
        self,
        chain_keys: Sequence[str] | None = None,
        trigger: str = "cli",
        wallets: Sequence[str] | None = None,
    ) -> tuple[int, dict]:
        wallets = list(wallets) if wallets else [w["address"] for w in self.db.list_wallets()]
        if not wallets:
            raise ServiceError("add at least one wallet before scanning")
        if self.settings.demo:
            report = self._demo_report()
        else:
            report = self.build_scanner().scan(list(chain_keys or self.settings.chains or []), wallets)
        _apply_min_usd(report, self.settings.min_usd)
        payload = to_dict(report)
        snapshot_id = self.db.save_snapshot(
            chains=report.chains,
            wallets=report.wallets,
            totals=payload["totals"],
            report=payload,
            trigger=trigger,
        )
        self._generate_alerts(snapshot_id, payload)
        self._persist_datasets(payload)
        return snapshot_id, payload

    def job(self, job_id: str) -> dict:
        job = self.db.get_job(job_id)
        if not job:
            raise NotFound(f"job {job_id} not found")
        return job

    # --------------------------------------------------------------- alerts
    def _generate_alerts(self, snapshot_id: int, payload: dict) -> int:
        previous = self.db.previous_claims(snapshot_id)
        new_alerts: list[dict] = []
        active_keys: set[str] = set()

        def emit(
            *,
            kind: str,
            severity: str,
            title: str,
            body: str,
            wallet: str | None = None,
            drop_id: str | None = None,
            tier: str | None = None,
            persistent: bool = True,
        ) -> None:
            key = self.db.alert_key(
                kind, title, wallet=wallet, drop_id=drop_id, tier=tier
            )
            if persistent:
                active_keys.add(key)
            alert_id = self.db.add_alert(
                kind=kind,
                severity=severity,
                title=title,
                body=body,
                wallet=wallet,
                drop_id=drop_id,
                snapshot_id=snapshot_id,
                dedupe_key=key,
                tier=tier,
            )
            if alert_id is not None:
                new_alerts.append(
                    {
                        "id": alert_id,
                        "kind": kind,
                        "severity": severity,
                        "title": public_text(title),
                        "body": public_text(body),
                        "wallet": wallet,
                        "drop_id": drop_id,
                        "created_at": _now(),
                    }
                )

        for claim in payload["claims"]:
            wallet = claim["wallet"]
            drop = claim["name"]
            status = claim["status"]
            key = (claim["drop_id"], wallet)
            was = previous.get(key)

            if status == UNCLAIMED and not claim.get("expired"):
                net = claim.get("net_usd")
                if net is not None and net <= 0:
                    emit(
                        kind="below_gas",
                        severity="warning",
                        title=f"{drop} costs more gas than it is worth",
                        body=(
                            f"{wallet} · value ${claim.get('usd_value') or 0:.2f} vs gas "
                            f"${claim.get('gas_usd') or 0:.2f}. Skip unless gas drops."
                        ),
                        wallet=wallet,
                        drop_id=claim["drop_id"],
                    )
                deadline = _parse_deadline(claim.get("deadline"))
                if deadline and deadline - datetime.now(timezone.utc) < timedelta(days=14):
                    # The tier is part of the dedupe key, so the same condition
                    # can escalate from the 14-day reminder to the final-three-day
                    # warning without duplicating on every scan.  The older tier
                    # is resolved by the sweep below.
                    final_stretch = deadline - datetime.now(timezone.utc) <= timedelta(days=3)
                    tier = "3d" if final_stretch else "14d"
                    title = (
                        f"{drop} claim window closes in 3 days or less"
                        if final_stretch
                        else f"{drop} claim window closing"
                    )
                    emit(
                        kind="deadline",
                        severity="critical",
                        title=title,
                        body=(
                            f"{wallet} still has an unclaimed allocation; the window "
                            f"closes {deadline.strftime('%d %b %Y')}."
                        ),
                        wallet=wallet,
                        drop_id=claim["drop_id"],
                        tier=tier,
                    )
                newly_allocated = was == NO_ALLOCATION or (previous and was is None)
                if newly_allocated:
                    emit(
                        kind="new_allocation",
                        severity="info",
                        title=f"New allocation detected: {drop}",
                        body=f"{wallet} now has an unclaimed allocation.",
                        wallet=wallet,
                        drop_id=claim["drop_id"],
                        persistent=False,
                    )
                if claim.get("proof_verified") is False:
                    emit(
                        kind="invalid_proof",
                        severity="critical",
                        title=f"Invalid proof for {drop}",
                        body=f"{wallet}: dataset proof does not match the on-chain root. Do not trust it.",
                        wallet=wallet,
                        drop_id=claim["drop_id"],
                    )

            if status == CLAIMED and was == UNCLAIMED:
                emit(
                    kind="claimed",
                    severity="info",
                    title=f"Claim confirmed: {drop}",
                    body=f"{wallet} was unclaimed in the previous snapshot and is now claimed.",
                    wallet=wallet,
                    drop_id=claim["drop_id"],
                    persistent=False,
                )

            if status == ERROR:
                emit(
                    kind="claim_error",
                    severity="critical",
                    title=f"Claim check failed: {drop}",
                    body=claim.get("note") or "unknown error",
                    wallet=wallet,
                    drop_id=claim["drop_id"],
                )

        for row in self.db.list_airdrops(enabled_only=True):
            payload_drop = row["payload"]
            eligibility = payload_drop.get("eligibility") or {}
            dataset = eligibility.get("dataset")
            if dataset and not _dataset_exists(str(dataset)):
                emit(
                    kind="missing_dataset",
                    severity="warning",
                    title=f"{payload_drop.get('name', row['id'])}: claims dataset missing",
                    body=(
                        f"Add {Path(str(dataset)).name} to the configured dataset directory, then run a scan. "
                        "The drop cannot be checked until the file is available."
                    ),
                    drop_id=row["id"],
                )
            if not payload_drop.get("eligibility"):
                emit(
                    kind="missing_eligibility",
                    severity="warning",
                    title=f"{payload_drop.get('name', row['id'])} has no eligibility source",
                    body="Add a dataset, claim API or allocation view to make this drop checkable.",
                    drop_id=row["id"],
                )

        scanned_drops = sorted({str(claim["drop_id"]) for claim in payload["claims"]})
        self.db.resolve_stale_alerts(
            active_keys,
            kinds=CONDITION_ALERT_KINDS,
            drop_ids=scanned_drops,
        )
        if new_alerts:
            self.notifier.dispatch(new_alerts)
        return len(new_alerts)

    def alerts(self, *, include_ack: bool = False, include_resolved: bool = False) -> list[dict]:
        rows = self.db.list_alerts(
            include_ack=include_ack,
            include_resolved=include_resolved or include_ack,
        )
        for row in rows:
            row["title"] = public_text(row["title"])
            row["body"] = public_text(row["body"])
            if row.get("resolved_reason"):
                row["resolved_reason"] = public_text(row["resolved_reason"])
        return rows

    def ack_alert(self, alert_id: int) -> bool:
        self._require_local_edits("acknowledge an alert")
        return self.db.ack_alert(alert_id)

    def ack_all_alerts(self) -> int:
        self._require_local_edits("acknowledge alerts")
        return self.db.ack_all_alerts()

    def resolve_alert(self, alert_id: int, reason: str = "resolved by operator") -> bool:
        self._require_local_edits("resolve an alert")
        return self.db.resolve_alert(alert_id, reason)

    def reopen_alert(self, alert_id: int) -> bool:
        self._require_local_edits("reopen an alert")
        try:
            return self.db.reopen_alert(alert_id)
        except sqlite3.IntegrityError:
            raise Conflict(
                "another open alert already covers this condition; acknowledge or resolve it first"
            ) from None

    def notifications(self, limit: int = 50) -> list[dict]:
        rows = self.db.list_notifications(limit)
        for row in rows:
            if row.get("error"):
                row["error"] = public_text(row["error"])
        return rows

    # ------------------------------------------------------- claim execution
    def prepare_claim(self, drop_id: str, wallet: str) -> dict:
        """Prepare an unsigned claim only after a fresh, fail-closed preflight.

        Every check below runs against the chain now, not against the snapshot.
        A failure is recorded in executions/audit and returned as an error; no
        partial transaction is ever handed out.
        """
        self._require_local_edits("prepare a claim")
        self._require_write_mode("prepare claim transaction")
        snapshot = self.db.latest_snapshot()
        if not snapshot:
            raise NotFound("no snapshot yet - run a scan first")
        row = self.db.claim(snapshot["id"], drop_id, wallet)
        if not row:
            raise NotFound("no claim position for that wallet/drop in the latest snapshot")
        if row["status"] != UNCLAIMED:
            raise Conflict(f"position is {row['status']}, not claimable")

        drop = next((item for item in self.registry_drops() if item.id == drop_id), None)
        if drop is None:
            raise NotFound(f"airdrop {drop_id} is not in the registry")
        chain = self.chains.get(row["chain"])

        deadline = _parse_deadline(drop.claim_deadline)
        if deadline is not None and deadline <= datetime.now(timezone.utc):
            self._record_preflight_failure(row, "claim window expired before preparation")
            raise Conflict("claim window has expired (checked again at preparation time)")
        if not row["calldata"]:
            self._record_preflight_failure(row, "no calldata in the snapshot")
            raise Conflict("no calldata available for this position")

        rpc = self._chain_rpc(chain)
        try:
            evidence = run_preflight(rpc, drop, row)
        except PreflightError as exc:
            self._record_preflight_failure(row, str(exc))
            error_class = Conflict if exc.status == 409 else ServiceError
            if exc.status == 503:
                error_class = ServiceError
            raise error_class(str(exc)) from None

        gas_limit = row.get("gas_limit")
        tx = {
            "chainId": hex(chain.chain_id),
            "from": row["wallet"],
            "to": row["distributor"],
            "value": "0x0",
            "data": row["calldata"],
        }
        if gas_limit:
            tx["gasLimit"] = hex(int(gas_limit))
        if row.get("fee_model") == "eip1559" and row.get("max_fee_per_gas"):
            tx["maxFeePerGas"] = hex(int(row["max_fee_per_gas"]))
            tx["maxPriorityFeePerGas"] = hex(int(row["max_priority_fee_per_gas"] or 0))
        elif row.get("fee_per_gas"):
            tx["gasPrice"] = hex(int(row["fee_per_gas"]))
        assert tx["value"] == "0x0", "claim value must always be zero"

        call = evidence["call"]
        amount_display = Decimal(int(row["amount_raw"])) / (Decimal(10) ** int(row["decimals"]))
        prepared_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        summary = [
            f"Network: {chain.name} (chain id {chain.chain_id}).",
            f"Contract: {drop.name} distributor {drop.distributor}.",
            f"Function: {call['function']}.",
            f"Index: {call['index']}; account: {call['account']}; amount: {amount_display} "
            f"({row['amount_raw']} base units).",
            f"Proof: {len(call['proof'])} sibling hashes, re-verified against the on-chain root.",
            "Value: 0 - this claim never asks you to send ETH or a token.",
            "The server does not sign or broadcast; your wallet will.",
        ]
        self.db.add_execution(
            wallet=row["wallet"],
            drop_id=drop_id,
            chain=row["chain"],
            tx_hash=None,
            status="prepared",
            detail=f"preflight ok at block {evidence.get('block_number')}",
        )
        self.db.log(
            "api",
            "claim.prepare",
            f"{drop_id}/{row['wallet']} preflight ok at block {evidence.get('block_number')}",
        )
        return {
            "transaction": tx,
            "claim": {
                "drop_id": drop_id,
                "name": row["name"],
                "chain": row["chain"],
                "amount": row["amount_raw"],
                "decimals": row["decimals"],
                "usd_value": row["usd_value"],
                "gas_usd": row["gas_usd"],
                "gas_usd_buffered": row.get("gas_cost_usd_buffered"),
                "gas_estimate_quality": row.get("gas_estimate_quality"),
                "gas_note": row.get("gas_note"),
                "net_usd": row["net_usd"],
                "distributor": row["distributor"],
            },
            "preflight": {
                "checked_at": prepared_at,
                "block_number": evidence.get("block_number"),
                "call": call,
                "root": evidence.get("root"),
                "distributor": row["distributor"],
                "explorer_url": (
                    f"{chain.explorer}/address/{row['distributor']}" if chain.explorer else None
                ),
                "payable": evidence.get("payable"),
                "provenance": evidence.get("provenance"),
            },
            "what_you_are_about_to_sign": summary,
            "warnings": [
                "Check the destination address against the project's official docs before signing.",
                "A legitimate claim never needs a token approval or an off-chain signature.",
                "This server does not broadcast; your wallet does.",
            ],
        }

    def _new_rpc(self, chain, *, quorum: int | None = None) -> Rpc:
        return Rpc(
            self.settings.rpc_endpoints(chain),
            quorum=quorum if quorum is not None else self.settings.rpc_quorum,
            health=self.endpoint_health,
            label=chain.key,
        )

    def _chain_rpc(self, chain) -> Rpc:
        if self.rpc_factory:
            return self.rpc_factory(chain)
        # The highest-risk action always uses two endpoints when they exist.
        quorum = max(2, self.settings.rpc_quorum) if len(self.settings.rpc_endpoints(chain)) >= 2 else 1
        return self._new_rpc(chain, quorum=quorum)

    def rpc_health(self) -> list[dict]:
        return self.endpoint_health.snapshot()

    def _persist_datasets(self, payload: dict) -> None:
        for record in payload.get("datasets", []) or []:
            try:
                self.db.upsert_dataset_provenance(record)
            except (KeyError, sqlite3.Error):
                continue

    def _record_preflight_failure(self, row: dict, detail: str) -> None:
        self.db.add_execution(
            wallet=row["wallet"],
            drop_id=row["drop_id"],
            chain=row["chain"],
            tx_hash=None,
            status="preflight_failed",
            detail=detail[:500],
        )
        self.db.log(
            "api",
            "claim.preflight_failed",
            f"{row['drop_id']}/{row['wallet']}: {detail[:300]}",
        )

    def confirm_claim(self, *, drop_id: str, wallet: str, tx_hash: str) -> dict:
        self._require_local_edits("record a claim")
        self._require_write_mode("record a claim transaction")
        if not TX_HASH.match(tx_hash or ""):
            raise ServiceError("tx_hash must be a 32-byte hex transaction hash")
        snapshot = self.db.latest_snapshot()
        row = self.db.claim(snapshot["id"], drop_id, wallet) if snapshot else None
        if not row:
            raise NotFound("no claim position for that wallet/drop in the latest snapshot")

        status = "submitted"
        detail = "recorded by user; will show as claimed after the next scan"
        receipt = self._receipt(row["chain"], tx_hash)
        if receipt is not None:
            status = "confirmed" if receipt.get("status") == "0x1" else "failed"
            detail = f"receipt status {receipt.get('status')}"
        self.db.add_execution(
            wallet=wallet,
            drop_id=drop_id,
            chain=row["chain"],
            tx_hash=tx_hash,
            status=status,
            detail=detail,
        )
        self.db.log("api", "claim.confirm", f"{drop_id}/{wallet}/{tx_hash} ({status})")
        return {"status": status, "tx_hash": tx_hash, "detail": detail, "chain": row["chain"]}

    def _receipt(self, chain_key: str, tx_hash: str) -> dict | None:
        chain = self.chains.chains.get(chain_key)
        if not chain:
            return None
        try:
            rpc = (
                self.rpc_factory
                or (lambda c: Rpc(self.settings.rpc_endpoints(c), timeout=8, retries=1, label=c.key))
            )(chain)
            return rpc.request("eth_getTransactionReceipt", tx_hash)
        except Exception:  # noqa: BLE001 - receipt is best-effort
            return None

    def _require_write_mode(self, action: str) -> None:
        if self.settings.read_only:
            raise WriteDisabled(
                f"read-only mode: refusing to {action}. Set IDLESCAN_READ_ONLY=false in .env "
                "to enable the claim hand-off endpoints. The server still never holds keys."
            )

    def executions(self, limit: int = 50) -> list[dict]:
        return self.db.list_executions(limit)

    # --------------------------------------------------------------- exports
    def export(self, fmt: str) -> tuple[str, str, bytes]:
        snapshot = self.db.latest_snapshot()
        if not snapshot:
            raise NotFound("no snapshot to export")
        stamp = snapshot["created_at"][:10]
        if fmt == "json":
            return (f"idlescan-{stamp}.json", "application/json", (json.dumps(snapshot["report"], indent=2) + "\n").encode())
        if fmt == "csv":
            buffer = io.StringIO()
            buffer.write(
                "kind,chain,wallet,asset_or_drop,status,amount,usd_price,usd_value,gas_usd,net_usd,note,reference\n"
            )
            for holding in self.db.holdings(snapshot["id"]):
                buffer.write(
                    f"balance,{holding['chain']},{holding['wallet']},{holding['asset']},,"
                    f"{holding['raw']},,,,,\n"
                )
            for claim in self.db.claims(snapshot["id"]):
                buffer.write(
                    f"claim,{claim['chain']},{claim['wallet']},{claim['name']},{claim['status']},"
                    f"{claim['amount_raw']},{claim['usd_price'] or ''},{claim['usd_value'] or ''},"
                    f"{claim['gas_usd'] or ''},{claim['net_usd'] if claim['net_usd'] is not None else ''},"
                    f"\"{claim['note'] or ''}\",{claim['distributor']}\n"
                )
            return (f"idlescan-{stamp}.csv", "text/csv", buffer.getvalue().encode())
        if fmt in ("md", "markdown"):
            markdown = render_markdown(_report_from_dict(snapshot["report"]))
            return (f"idlescan-{stamp}.md", "text/markdown", markdown.encode())
        raise ServiceError(f"unknown export format {fmt!r} (json, csv, md)")

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict:
        data = self.db.stats()
        data["read_only"] = self.settings.read_only
        data["write_enabled"] = self.settings.write_enabled
        data["chains"] = len(self.chains.chains)
        data["tokens"] = sum(len(v) for v in self.tokens.values())
        return data

    def portfolio(self, snapshot_id: int | None = None) -> dict:
        snapshot = self.db.get_snapshot(snapshot_id) if snapshot_id else self.db.latest_snapshot()
        if not snapshot:
            return {"snapshot": None, "holdings": [], "claims": [], "wallets": self.db.list_wallets()}
        wallets = {w["address"]: w for w in self.db.list_wallets()}
        holdings = self.db.holdings(snapshot["id"])
        claims = self.db.claims(snapshot["id"])
        for row in holdings + claims:
            wallet = wallets.get(row["wallet"])
            row["wallet_label"] = (wallet or {}).get("label", "")
            row["wallet_tags"] = (wallet or {}).get("tags", "")
        return {
            "snapshot": {key: snapshot[key] for key in ("id", "created_at", "chains", "wallets", "trigger", "totals")},
            "holdings": holdings,
            "claims": claims,
            "wallets": self.db.list_wallets(),
        }

    def snapshots(self, limit: int = 30) -> list[dict]:
        return self.db.list_snapshots(limit)

    def audit(self, limit: int = 100) -> list[dict]:
        rows = self.db.list_audit(limit)
        for row in rows:
            row["detail"] = public_text(row["detail"])
        return rows

    # ------------------------------------------------------------ airdrops API
    def airdrops(self) -> list[dict]:
        latest = self.db.latest_snapshot()
        positions: dict[str, list[dict]] = {}
        if latest:
            for row in self.db.claims(latest["id"]):
                positions.setdefault(row["drop_id"], []).append(row)
        out = []
        for row in self.db.list_airdrops():
            payload = row["payload"]
            rows = positions.get(row["id"], [])
            unclaimed = [r for r in rows if r["status"] == UNCLAIMED and not r["expired"]]
            days_left = _days_left(payload.get("claim_deadline"))
            out.append(
                {
                    "id": row["id"],
                    "enabled": bool(row["enabled"]),
                    "source": row["source"],
                    "name": payload.get("name", row["id"]),
                    "chain": payload.get("chain"),
                    "distributor": payload.get("distributor"),
                    "token": payload.get("token"),
                    "eligibility": (payload.get("eligibility") or {}).get("type"),
                    "has_dataset": bool((payload.get("eligibility") or {}).get("dataset")),
                    "deadline": payload.get("claim_deadline"),
                    "notes": payload.get("notes"),
                    "links": payload.get("links") or {},
                    "positions": len(rows),
                    "unclaimed": len(unclaimed),
                    "unclaimed_usd": sum(r["usd_value"] or 0 for r in unclaimed),
                    "claimable_net_usd": sum(r["net_usd"] or 0 for r in unclaimed),
                    "gas_usd": sum(r["gas_usd"] or 0 for r in unclaimed),
                    "days_left": days_left,
                    "phase": _phase(rows, unclaimed, days_left),
                    "risk": _risk(payload, rows, row["enabled"]),
                }
            )
        return out

    def add_airdrop(self, payload: dict) -> dict:
        self._require_local_edits("add an airdrop")
        candidate = dict(payload)
        candidate.setdefault("token_decimals", 18)
        try:
            drops = parse_registry({"airdrops": [candidate]}, self.chains)
        except ConfigError as exc:
            raise ServiceError(str(exc)) from None
        drop = drops[0]
        existing = self.db.get_airdrop(drop.id)
        if existing and existing["source"] != "custom":
            raise Conflict(
                f"airdrop {drop.id} is managed internally ({existing['source']}); choose another id"
            )
        self.db.upsert_airdrop(drop.id, _drop_payload(drop), enabled=True, source="custom")
        self.db.log("api", "airdrop.add", drop.id)
        return next(a for a in self.airdrops() if a["id"] == drop.id)

    def set_airdrop_enabled(self, drop_id: str, enabled: bool) -> None:
        self._require_local_edits("change an airdrop")
        if not self.db.set_airdrop_enabled(drop_id, enabled):
            raise NotFound(f"airdrop {drop_id} not found")

    def delete_airdrop(self, drop_id: str) -> None:
        self._require_local_edits("delete an airdrop")
        if not self.db.delete_airdrop(drop_id):
            raise Conflict("bundled registry entries cannot be deleted; disable them instead")
        self.db.log("api", "airdrop.delete", drop_id)

    # -------------------------------------------------------- explorer views
    def airdrop_detail(self, drop_id: str) -> dict:
        row = self.db.get_airdrop(drop_id)
        if not row:
            raise NotFound(f"airdrop {drop_id} not found")
        summary = next((a for a in self.airdrops() if a["id"] == drop_id), None)
        if not summary:
            raise NotFound(f"airdrop {drop_id} not found")
        payload = row["payload"]
        latest = self.db.latest_snapshot()
        positions = self.db.claims(latest["id"], drop_id=drop_id) if latest else []
        wallets = {w["address"]: w for w in self.db.list_wallets()}
        for position in positions:
            wallet = wallets.get(position["wallet"], {})
            position["wallet_label"] = wallet.get("label", "")
            position["wallet_tags"] = wallet.get("tags", "")

        order = ("unclaimed", "claimed", "expired", "no_allocation", "unknown", "error")
        buckets: dict[str, dict] = {}
        for position in positions:
            key = "expired" if position["expired"] else position["status"]
            bucket = buckets.setdefault(key, {"status": key, "count": 0, "usd_value": 0.0, "net_usd": 0.0})
            bucket["count"] += 1
            bucket["usd_value"] += position["usd_value"] or 0
            bucket["net_usd"] += position["net_usd"] or 0
        status_breakdown = sorted(
            buckets.values(), key=lambda item: order.index(item["status"]) if item["status"] in order else 99
        )

        proof = {"verified": 0, "mismatch": 0, "unverified": 0}
        for position in positions:
            value = position["proof_verified"]
            proof["verified" if value == 1 else "mismatch" if value == 0 else "unverified"] += 1

        chain = self.chains.chains.get(summary["chain"] or "")
        distributor = payload.get("distributor")
        token = payload.get("token")
        explorer = chain.explorer if chain else None
        contract = {
            "distributor": distributor,
            "distributor_url": f"{explorer}/address/{distributor}" if explorer and distributor else None,
            "token": token,
            "token_url": f"{explorer}/token/{token}" if explorer and token else None,
            "token_decimals": payload.get("token_decimals", 18),
            "claim_method": payload.get("claim_method"),
            "claimed_method": payload.get("claimed_method"),
            "root_method": payload.get("root_method"),
            "explorer": explorer,
            "chain_id": chain.chain_id if chain else None,
        }

        eligibility = payload.get("eligibility")
        if eligibility and eligibility.get("dataset"):
            eligibility = {**eligibility, "dataset": Path(eligibility["dataset"]).name}

        return {
            **summary,
            "contract": contract,
            "eligibility": eligibility,
            "dataset_provenance": self.db.dataset_provenance(drop_id),
            "verified_on": payload.get("verified_on"),
            "notes": payload.get("notes"),
            "positions": positions,
            "wallet_count": len({p["wallet"] for p in positions}),
            "status_breakdown": status_breakdown,
            "proof": proof,
            "history": self.db.drop_history(drop_id),
            "executions": [e for e in self.executions(200) if e["drop_id"] == drop_id][:12],
            "alerts": [a for a in self.alerts(include_ack=True) if a["drop_id"] == drop_id][:12],
            "export": {
                "json": f"/api/airdrops/{drop_id}/export/json",
                "csv": f"/api/airdrops/{drop_id}/export/csv",
                "md": f"/api/airdrops/{drop_id}/export/md",
            },
        }

    # ------------------------------------------------------------ custom tokens
    def custom_tokens(self, chain: str | None = None) -> list[dict]:
        return self.db.list_custom_tokens(chain)

    def add_custom_token(
        self,
        chain: str,
        address: str,
        symbol: str,
        decimals: int,
        *,
        source: str = "manual",
    ) -> dict:
        self._require_local_edits("add a custom token")
        self._require_write_mode("add custom token")
        self.chains.get(chain)  # raises ConfigError for unknown chain
        result = self.db.add_custom_token(chain, address, symbol, decimals, source=source)
        if result is None:
            raise Conflict(
                f"custom token {address} already tracked on {chain}"
            )
        self.db.log("api", "custom_token.add", f"{chain}:{address}")
        return result

    def remove_custom_token(self, chain: str, address: str) -> bool:
        self._require_local_edits("remove a custom token")
        self._require_write_mode("remove custom token")
        removed = self.db.remove_custom_token(chain, address)
        if removed:
            self.db.log("api", "custom_token.remove", f"{chain}:{address}")
        return removed

    # ------------------------------------------------------------ wallets
    def wallet_detail(self, address: str) -> dict:
        try:
            normalised = normalise_address(address)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        wallet = self.db.get_wallet(normalised)
        if not wallet:
            raise NotFound(f"wallet {normalised} is not tracked")
        snapshot = self.db.latest_snapshot()
        holdings = self.db.holdings(snapshot["id"], wallet=normalised) if snapshot else []
        claims = self.db.claims(snapshot["id"], wallet=normalised) if snapshot else []
        executions = [e for e in self.executions(200) if e["wallet"] == normalised][:12]
        alerts = [a for a in self.alerts(include_ack=True) if a["wallet"] == normalised][:12]
        unclaimed = [c for c in claims if c["status"] == UNCLAIMED and not c["expired"]]
        claimed = [c for c in claims if c["status"] == CLAIMED]

        chains: dict[str, dict] = {}
        for holding in holdings:
            item = chains.setdefault(holding["chain"], {"chain": holding["chain"], "idle_usd": 0.0, "unclaimed_usd": 0.0})
            item["idle_usd"] += holding["usd_value"] or 0
        for claim in unclaimed:
            item = chains.setdefault(claim["chain"], {"chain": claim["chain"], "idle_usd": 0.0, "unclaimed_usd": 0.0})
            item["unclaimed_usd"] += claim["usd_value"] or 0

        return {
            "wallet": wallet,
            "snapshot": (
                {key: snapshot[key] for key in ("id", "created_at", "chains", "wallets", "trigger", "totals")}
                if snapshot
                else None
            ),
            "holdings": holdings,
            "claims": claims,
            "history": self.db.wallet_history(normalised),
            "executions": executions,
            "alerts": alerts,
            "chains": sorted(chains.values(), key=lambda item: item["idle_usd"] + item["unclaimed_usd"], reverse=True),
            "totals": {
                "idle_usd": sum(h["usd_value"] or 0 for h in holdings),
                "unclaimed_usd": sum(c["usd_value"] or 0 for c in unclaimed),
                "net_usd": sum(c["net_usd"] or 0 for c in unclaimed),
                "claimed_usd": sum(c["usd_value"] or 0 for c in claimed),
                "gas_usd": sum(c["gas_usd"] or 0 for c in unclaimed),
                "assets": len(holdings),
                "positions": len(claims),
                "unclaimed": len(unclaimed),
                "claimed": len(claimed),
                "chains": len({h["chain"] for h in holdings} | {c["chain"] for c in claims}),
            },
        }

    def check_address(self, address: str) -> dict:
        """Check one address across every enabled drop without tracking it.

        This is the public "eligibility checker" path: nothing is written to the
        wallet registry or the snapshot history. In demo mode the synthetic
        ledger is filtered by address; otherwise one read-only scan runs for
        that address alone.
        """
        try:
            normalised = normalise_address(address)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None

        with self._check_lock:
            if self.settings.demo:
                report = self._demo_report()
            else:
                report = self.build_scanner().scan(list(self.settings.chains or []), [normalised])
            _apply_min_usd(report, self.settings.min_usd)
            payload = to_dict(report)

        claims = [row for row in payload.get("claims", []) if row["wallet"] == normalised]
        holdings = [row for row in payload.get("holdings", []) if row["wallet"] == normalised]
        unclaimed = [row for row in claims if row["status"] == UNCLAIMED and not row["expired"]]
        claimed = [row for row in claims if row["status"] == CLAIMED]
        tracked = self.db.get_wallet(normalised)

        phases = {drop["id"]: drop["phase"] for drop in self.airdrops()}
        for claim in claims:
            claim["phase"] = phases.get(claim["drop_id"], "tracking")

        return {
            "address": normalised,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "demo": self.settings.demo,
            "tracked": bool(tracked),
            "wallet": tracked,
            "chains": payload.get("chains", []),
            "warnings": payload.get("warnings", []),
            "holdings": holdings,
            "claims": claims,
            "totals": {
                "idle_usd": sum(row["usd_value"] or 0 for row in holdings),
                "unclaimed_usd": sum(row["usd_value"] or 0 for row in unclaimed),
                "net_usd": sum(row["net_usd"] or 0 for row in unclaimed),
                "gas_usd": sum(row["gas_usd"] or 0 for row in unclaimed),
                "claimed_usd": sum(row["usd_value"] or 0 for row in claimed),
                "assets": len(holdings),
                "positions": len(claims),
                "unclaimed": len(unclaimed),
                "claimed": len(claimed),
                "drops": len({row["drop_id"] for row in claims}),
            },
        }

    def add_wallets(self, entries: Sequence[dict | str]) -> dict:
        self._require_local_edits("import wallets")
        """Bulk watchlist import. Invalid rows are reported, valid rows still land."""
        if not isinstance(entries, (list, tuple)):
            raise ServiceError("'wallets' must be a list")
        if not entries:
            raise ServiceError("provide at least one wallet to import")
        if len(entries) > 200:
            raise ServiceError("import at most 200 wallets at a time")

        added: list[dict] = []
        updated: list[dict] = []
        errors: list[dict] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            if isinstance(entry, dict):
                raw_address = str(entry.get("address", ""))
                label = str(entry.get("label", ""))
                tags = str(entry.get("tags", ""))
            else:
                raw_address = str(entry)
                label = ""
                tags = ""
            try:
                normalised = normalise_address(raw_address)
            except ValueError as exc:
                errors.append({"index": index, "address": raw_address[:80], "error": str(exc)})
                continue
            if normalised in seen:
                errors.append({"index": index, "address": normalised, "error": "duplicate address in import"})
                continue
            seen.add(normalised)
            existed = self.db.get_wallet(normalised) is not None
            wallet = self.db.add_wallet(normalised, label.strip()[:60], tags.strip()[:120])
            (updated if existed else added).append(wallet)

        if added or updated:
            self.db.log(
                "api",
                "wallet.bulk",
                f"added {len(added)}, updated {len(updated)}, failed {len(errors)}",
            )
        return {"added": len(added), "updated": len(updated), "wallets": added + updated, "errors": errors}

    def analytics(self) -> dict:
        latest = self.portfolio()
        snapshot = latest["snapshot"]
        claims = latest["claims"]
        holdings = latest["holdings"]
        wallets = latest["wallets"] or []

        history = [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "idle_usd": (row["totals"] or {}).get("idle_usd") or 0,
                "unclaimed_usd": (row["totals"] or {}).get("unclaimed_usd") or 0,
                "combined_usd": (row["totals"] or {}).get("combined_usd") or 0,
            }
            for row in reversed(self.db.list_snapshots(40))
        ]

        by_chain: dict[str, dict] = {}
        for holding in holdings:
            item = by_chain.setdefault(holding["chain"], {"chain": holding["chain"], "usd_value": 0.0, "holdings": 0})
            item["usd_value"] += holding["usd_value"] or 0
            item["holdings"] += 1
        chain_breakdown = sorted(by_chain.values(), key=lambda item: item["usd_value"], reverse=True)

        status_order = ("unclaimed", "claimed", "expired", "no_allocation", "unknown", "error")
        by_status: dict[str, dict] = {}
        for claim in claims:
            key = "expired" if claim["expired"] else claim["status"]
            item = by_status.setdefault(key, {"status": key, "count": 0, "usd_value": 0.0, "net_usd": 0.0})
            item["count"] += 1
            item["usd_value"] += claim["usd_value"] or 0
            item["net_usd"] += claim["net_usd"] or 0
        status_breakdown = sorted(
            by_status.values(), key=lambda item: status_order.index(item["status"]) if item["status"] in status_order else 99
        )

        unclaimed = [c for c in claims if c["status"] == UNCLAIMED and not c["expired"]]
        claimed = [c for c in claims if c["status"] == CLAIMED]
        unclaimed_value = sum(c["usd_value"] or 0 for c in unclaimed)
        claimed_value = sum(c["usd_value"] or 0 for c in claimed)
        denominator = unclaimed_value + claimed_value
        airdrops = self.airdrops()
        deadlines = sorted(
            [a for a in airdrops if a["days_left"] is not None and a["days_left"] >= 0],
            key=lambda item: item["days_left"],
        )[:8]
        active_wallets = {c["wallet"] for c in claims if c["status"] != NO_ALLOCATION}

        return {
            "snapshot": snapshot,
            "history": history,
            "totals": {
                "idle_usd": snapshot["totals"].get("idle_usd") if snapshot else 0,
                "unclaimed_usd": unclaimed_value,
                "claimed_usd": claimed_value,
                "net_usd": sum(c["net_usd"] or 0 for c in unclaimed),
                "gas_usd": sum(c["gas_usd"] or 0 for c in unclaimed),
                "recovery_rate": (claimed_value / denominator) if denominator else None,
                "positions": len(claims),
                "wallets": len(wallets),
            },
            "chain_breakdown": chain_breakdown,
            "status_breakdown": status_breakdown,
            "deadlines": deadlines,
            "top_drops": sorted(airdrops, key=lambda item: item["claimable_net_usd"], reverse=True)[:5],
            "coverage": {
                "wallets": len(wallets),
                "with_positions": len(active_wallets),
                "ratio": (len(active_wallets) / len(wallets)) if wallets else None,
            },
            "diff": self.snapshot_diff(snapshot["id"]) if snapshot else None,
        }

    def snapshot_diff(self, snapshot_id: int | None = None) -> dict:
        snapshot = self.db.get_snapshot(snapshot_id) if snapshot_id else self.db.latest_snapshot()
        if not snapshot:
            raise NotFound("no snapshot yet")
        previous = self.db.previous_snapshot(snapshot["id"])
        current_rows = self.db.claims(snapshot["id"])
        previous_rows = self.db.claims(previous["id"]) if previous else []
        current = {(row["drop_id"], row["wallet"]): row for row in current_rows}
        before = {(row["drop_id"], row["wallet"]): row for row in previous_rows}
        if not previous:
            current, before = {}, {}

        changes: list[dict] = []
        for key, row in current.items():
            earlier = before.get(key)
            base = {
                "drop_id": row["drop_id"],
                "name": row["name"],
                "chain": row["chain"],
                "wallet": row["wallet"],
                "status": row["status"],
                "expired": bool(row["expired"]),
                "usd_value": row["usd_value"],
                "net_usd": row["net_usd"],
            }
            if earlier is None:
                changes.append({**base, "kind": "new", "from": None, "to": row["status"],
                                "delta": row["net_usd"] or 0, "previous_net_usd": None})
                continue
            if row["status"] == CLAIMED and earlier["status"] != CLAIMED:
                changes.append({**base, "kind": "claimed", "from": earlier["status"], "to": row["status"],
                                "delta": 0.0, "previous_net_usd": earlier["net_usd"]})
                continue
            if row["expired"] and not earlier["expired"]:
                changes.append({**base, "kind": "expired", "from": earlier["status"], "to": "expired",
                                "delta": 0.0, "previous_net_usd": earlier["net_usd"]})
                continue
            if row["proof_verified"] != earlier["proof_verified"]:
                changes.append({**base, "kind": "proof", "from": earlier["proof_verified"],
                                "to": row["proof_verified"], "delta": 0.0,
                                "previous_net_usd": earlier["net_usd"]})
                continue
            if row["status"] != earlier["status"]:
                changes.append({**base, "kind": "status", "from": earlier["status"], "to": row["status"],
                                "delta": 0.0, "previous_net_usd": earlier["net_usd"]})
                continue
            if (
                row["status"] == UNCLAIMED
                and not row["expired"]
                and row["net_usd"] is not None
                and earlier["net_usd"] is not None
                and abs(row["net_usd"] - earlier["net_usd"]) >= 1.0
            ):
                changes.append({**base, "kind": "value", "from": earlier["status"], "to": row["status"],
                                "delta": row["net_usd"] - earlier["net_usd"],
                                "previous_net_usd": earlier["net_usd"]})
        for key, row in before.items():
            if key in current:
                continue
            changes.append({
                "kind": "removed", "drop_id": row["drop_id"], "name": row["name"], "chain": row["chain"],
                "wallet": row["wallet"], "status": row["status"], "expired": bool(row["expired"]),
                "usd_value": row["usd_value"], "net_usd": row["net_usd"], "from": row["status"],
                "to": None, "delta": 0.0, "previous_net_usd": row["net_usd"],
            })

        priority = {"claimed": 0, "new": 1, "expired": 2, "proof": 3, "status": 4, "value": 5, "removed": 6}
        changes.sort(key=lambda item: (priority.get(item["kind"], 9), -(abs(item.get("delta") or 0))))
        counts: dict[str, int] = {}
        for change in changes:
            counts[change["kind"]] = counts.get(change["kind"], 0) + 1
        current_totals = snapshot["totals"] or {}
        previous_totals = (previous["totals"] or {}) if previous else {}
        delta = {
            key: (current_totals.get(key) or 0) - (previous_totals.get(key) or 0)
            for key in ("idle_usd", "unclaimed_usd", "combined_usd")
        } if previous else None

        return {
            "snapshot": {"id": snapshot["id"], "created_at": snapshot["created_at"], "trigger": snapshot["trigger"]},
            "previous": (
                {"id": previous["id"], "created_at": previous["created_at"], "trigger": previous["trigger"]}
                if previous
                else None
            ),
            "changes": changes,
            "counts": counts,
            "totals": current_totals,
            "delta": delta,
        }

    def search(self, query: str, limit: int = 6) -> dict:
        needle = (query or "").strip().lower()
        if len(needle) < 2:
            return {"query": needle, "drops": [], "wallets": [], "transactions": []}
        drops = [
            {
                "id": drop["id"],
                "name": drop["name"],
                "chain": drop["chain"],
                "phase": drop["phase"],
                "unclaimed": drop["unclaimed"],
                "net_usd": drop["claimable_net_usd"],
            }
            for drop in self.airdrops()
            if needle in " ".join(
                str(drop.get(key) or "") for key in ("id", "name", "chain", "distributor", "token")
            ).lower()
        ][:limit]
        wallets = [
            {
                "address": wallet["address"],
                "label": wallet.get("label") or "",
                "tags": wallet.get("tags") or "",
                "idle_usd": wallet.get("idle_usd") or 0,
                "unclaimed_usd": wallet.get("unclaimed_usd") or 0,
            }
            for wallet in self.list_wallets()
            if needle in " ".join(
                str(wallet.get(key) or "") for key in ("address", "label", "tags")
            ).lower()
        ][:limit]
        transactions = [
            {
                "tx_hash": execution["tx_hash"],
                "drop_id": execution["drop_id"],
                "wallet": execution["wallet"],
                "status": execution["status"],
                "created_at": execution["created_at"],
            }
            for execution in self.executions(200)
            if execution.get("tx_hash") and needle in execution["tx_hash"].lower()
        ][:limit]
        return {"query": needle, "drops": drops, "wallets": wallets, "transactions": transactions}

    def calendar_ics(self, drop_id: str | None = None) -> bytes:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//idlescan//Airdrop deadlines//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "X-WR-CALNAME:idlescan airdrop deadlines",
        ]
        for drop in self.airdrops():
            if drop_id and drop["id"] != drop_id:
                continue
            deadline = _parse_deadline(drop.get("deadline"))
            if not deadline:
                continue
            start = deadline.date()
            description = [
                f"Chain: {drop['chain']}",
                f"Distributor: {drop['distributor']}",
                f"Tracked unclaimed: {drop['unclaimed']} position(s), ${drop['unclaimed_usd']:,.2f} gross",
            ]
            if drop.get("notes"):
                description.append(drop["notes"])
            link = (drop.get("links") or {}).get("claim") or (drop.get("links") or {}).get("website")
            name = drop["name"]
            event_summary = _ics_escape(f"Airdrop deadline: {name}")
            alarm_text = _ics_escape(f"Airdrop deadline in 3 days: {name}")
            lines += [
                "BEGIN:VEVENT",
                f"UID:{drop['id']}-deadline@idlescan.local",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(start + timedelta(days=1)).strftime('%Y%m%d')}",
                f"SUMMARY:{event_summary}",
                f"DESCRIPTION:{_ics_escape('; '.join(description))}",
                "CATEGORIES:AIRDROP",
            ]
            if link:
                lines.append(f"URL:{_ics_escape(link)}")
            lines += [
                "BEGIN:VALARM",
                "TRIGGER:-P3D",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{alarm_text}",
                "END:VALARM",
                "END:VEVENT",
            ]
        lines.append("END:VCALENDAR")
        return ("\r\n".join(_ics_fold(line) for line in lines) + "\r\n").encode()

    def export_drop(self, drop_id: str, fmt: str) -> tuple[str, str, bytes]:
        detail = self.airdrop_detail(drop_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", drop_id)
        if fmt == "json":
            return (
                f"idlescan-{safe_id}-{stamp}.json",
                "application/json",
                json.dumps(detail, indent=2, default=str).encode(),
            )
        if fmt == "csv":
            buffer = io.StringIO()
            writer = _csv_writer(buffer)
            writer.writerow([
                "drop", "status", "expired", "wallet", "label", "amount_raw", "decimals", "usd_value",
                "gas_usd", "net_usd", "proof_verified", "deadline", "note",
            ])
            for row in detail["positions"]:
                writer.writerow([
                    detail["name"], row["status"], "yes" if row["expired"] else "no", row["wallet"],
                    row.get("wallet_label") or "", row["amount_raw"], row["decimals"],
                    row["usd_value"] if row["usd_value"] is not None else "",
                    row["gas_usd"] if row["gas_usd"] is not None else "",
                    row["net_usd"] if row["net_usd"] is not None else "",
                    "" if row["proof_verified"] is None else ("yes" if row["proof_verified"] else "no"),
                    row["deadline"] or "", row["note"] or "",
                ])
            return f"idlescan-{safe_id}-{stamp}.csv", "text/csv", buffer.getvalue().encode()
        if fmt in ("md", "markdown"):
            proof = detail["proof"]
            lines = [
                f"# {detail['name']}",
                "",
                f"- Chain: `{detail['chain']}`",
                f"- Phase: **{detail['phase']}**",
                f"- Distributor: `{detail['distributor']}`",
                f"- Claim deadline: {detail['deadline'] or 'not documented'}",
                f"- Tracked positions: {len(detail['positions'])}",
                f"- Unclaimed: {detail['unclaimed']} position(s), ${detail['unclaimed_usd']:,.2f} gross, "
                f"${detail['claimable_net_usd']:,.2f} net after gas",
                f"- Proof checks: {proof['verified']} verified, {proof['mismatch']} mismatch, "
                f"{proof['unverified']} unverified",
                f"- Safety score: {detail['risk']['score']}/100 ({detail['risk']['label']} confidence)",
                "",
                "## Claim positions",
                "",
                "| Status | Wallet | Amount | Value | Gas | Net | Deadline | Proof |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
            ]
            for row in detail["positions"]:
                amount = f"{row['amount_raw']} raw"
                value = f"${row['usd_value']:,.2f}" if row["usd_value"] is not None else "—"
                gas = f"${row['gas_usd']:,.2f}" if row["gas_usd"] is not None else "—"
                net = f"${row['net_usd']:,.2f}" if row["net_usd"] is not None else "—"
                proof_text = {1: "verified", 0: "MISMATCH"}.get(row["proof_verified"], "unverified")
                lines.append(
                    f"| {row['status']} | `{row['wallet']}` | {amount} | {value} | {gas} | {net} | "
                    f"{row['deadline'] or '—'} | {proof_text} |"
                )
            lines += ["", "## Notes", "", detail.get("notes") or "No notes recorded.", ""]
            return f"idlescan-{safe_id}-{stamp}.md", "text/markdown", "\n".join(lines).encode()
        raise ServiceError(f"unknown export format {fmt!r} (json, csv, md)")

    def backup(self, target: Path | str | None = None) -> Path:
        self._require_local_edits("create a backup")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        if target:
            path = Path(target).expanduser()
            if path.is_dir():
                path = path / f"idlescan-{stamp}.db"
        else:
            path = self.settings.db_path.parent / "backups" / f"idlescan-{stamp}.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.db.path)
        destination = sqlite3.connect(path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        if not target or Path(target).expanduser().is_dir():
            self._rotate_backups(path.parent)
        self.db.log("system", "db.backup", path.name)
        return path

    def prune_database(self, **kwargs) -> dict:
        self._require_local_edits("prune the database")
        return self.db.prune(**kwargs)

    def _rotate_backups(self, directory: Path) -> list[Path]:
        """Keep only the newest ``IDLESCAN_BACKUP_KEEP`` backups in a directory."""
        keep = max(1, self.settings.backup_keep)
        candidates = sorted(
            (item for item in directory.glob("idlescan-*.db") if item.is_file()),
            key=lambda item: (item.stat().st_mtime, item.name),
            reverse=True,
        )
        removed: list[Path] = []
        for stale in candidates[keep:]:
            try:
                stale.unlink()
                removed.append(stale)
            except OSError:
                continue
        return removed

    # ------------------------------------------------------------------ demo
    def purge_demo_data(self) -> dict[str, int]:
        """Remove leftover demo rows when demo mode is off.

        Returns per-table counts.  Safe to call on every normal start: if there
        is nothing demo-related, every count is zero.
        """
        from .selftest import WALLET_CLAIMED, WALLET_EMPTY, WALLET_UNCLAIMED

        counts = self.db.purge_demo_records(
            [WALLET_CLAIMED, WALLET_EMPTY, WALLET_UNCLAIMED]
        )
        if any(counts.values()):
            self.db.log("system", "demo.purge", json.dumps(counts, sort_keys=True))
        return counts

    def seed_demo(self) -> None:
        if self.db.latest_snapshot() or self.db.list_wallets():
            return
        from .selftest import WALLET_CLAIMED, WALLET_EMPTY, WALLET_UNCLAIMED, build_config

        self.db.add_wallet(WALLET_UNCLAIMED, "Demo cold wallet", "demo")
        self.db.add_wallet(WALLET_CLAIMED, "Demo hot wallet", "demo")
        self.db.add_wallet(WALLET_EMPTY, "Demo watch-only", "demo")
        _, _, drops = build_config()
        for drop in drops:
            drop.claim_deadline = _demo_deadline()
            if drop.eligibility:
                drop.eligibility.dataset = None  # demo claims come from the mock chain
            self.db.upsert_airdrop(drop.id, _drop_payload(drop), enabled=True, source="demo")

        report = self._demo_report()
        payload = to_dict(report)
        snapshot_id = self.db.save_snapshot(
            chains=report.chains,
            wallets=report.wallets,
            totals=payload["totals"],
            report=payload,
            trigger="demo",
        )
        self._generate_alerts(snapshot_id, payload)
        self.db.log("system", "demo.seed", f"snapshot={snapshot_id}")

    def _demo_report(self) -> ScanReport:
        """Offline report with fixed prices, so every demo screen is populated."""
        from .selftest import run_selftest

        report = run_selftest()
        deadline = _demo_deadline()
        eth_usd, demo_usd = 2311.40, 2.04
        for holding in report.holdings:
            holding.usd_price = eth_usd if holding.symbol == "ETH" else 1.0
        for claim in report.claims:
            claim.deadline = deadline
            claim.expired = False
            claim.usd_price = demo_usd
            if claim.gas_native is not None:
                claim.gas_usd = claim.gas_native * eth_usd
            if claim.gas_cost_native_buffered is not None:
                claim.gas_cost_usd_buffered = claim.gas_cost_native_buffered * eth_usd
        return report


# --------------------------------------------------------------------- helpers
def _apply_min_usd(report: ScanReport, min_usd: float) -> None:
    if min_usd <= 0:
        return
    report.holdings = [
        holding
        for holding in report.holdings
        if holding.usd_value is None or holding.usd_value >= min_usd
    ]


def _dataset_exists(value: str) -> bool:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.exists()
    return (PROJECT_ROOT / path).exists()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _demo_deadline() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=9)).replace(microsecond=0).isoformat()


def _days_left(value: str | None) -> int | None:
    parsed = _parse_deadline(value)
    if not parsed:
        return None
    if len(str(value)) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return math.ceil((parsed - datetime.now(timezone.utc)).total_seconds() / 86400)


def _phase(rows: list[dict], unclaimed: list[dict], days_left: int | None) -> str:
    if days_left is not None and days_left < 0:
        return "expired"
    if unclaimed:
        return "closing" if days_left is not None and days_left <= 14 else "claimable"
    if rows and all(row["status"] == CLAIMED for row in rows):
        return "completed"
    if rows:
        return "no allocation"
    return "tracking"


def _ics_escape(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _ics_fold(line: str, limit: int = 75) -> str:
    """Fold an iCalendar content line on UTF-8 boundaries (RFC 5545 §3.1)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= limit:
        return line
    chunks: list[bytes] = []
    current = b""
    for character in line:
        data = character.encode("utf-8")
        cap = limit if not chunks else limit - 1  # continuation lines start with a space
        if current and len(current) + len(data) > cap:
            chunks.append(current)
            current = b""
        current += data
    if current:
        chunks.append(current)
    return "\r\n ".join(chunk.decode("utf-8") for chunk in chunks)


def _csv_writer(buffer):
    import csv

    return csv.writer(buffer, lineterminator="\n")


def _parse_deadline(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _drop_payload(drop: Airdrop) -> dict:
    payload: dict[str, Any] = {
        "id": drop.id,
        "name": drop.name,
        "chain": drop.chain,
        "distributor": drop.distributor,
        "token": drop.token,
        "token_decimals": drop.token_decimals,
        "claim_method": drop.claim_method,
        "claimed_method": drop.claimed_method,
        "root_method": drop.root_method or "",
        "claim_deadline": drop.claim_deadline,
        "notes": drop.notes,
        "links": drop.links,
        "verified_on": drop.verified_on,
        "enabled": drop.enabled,
    }
    if drop.eligibility:
        payload["eligibility"] = {
            "type": drop.eligibility.type,
            "dataset": drop.eligibility.dataset,
            "url": drop.eligibility.url,
            "method": drop.eligibility.method,
            "returns": drop.eligibility.returns,
            "decimals": drop.eligibility.decimals,
            "leaf_encoding": drop.eligibility.leaf_encoding,
            "double_hash": drop.eligibility.double_hash,
            "sort_pairs": drop.eligibility.sort_pairs,
        }
    return payload


def _risk(payload: dict, rows: list[dict], enabled: bool) -> dict:
    """Safety score from verifiable signals only - never a vibe."""
    score = 0
    reasons: list[str] = []
    if not enabled:
        reasons.append("disabled in registry")
    if payload.get("eligibility"):
        score += 30
        reasons.append("eligibility source configured")
    else:
        reasons.append("no eligibility source")
    if payload.get("links"):
        score += 10
        reasons.append("official links recorded")
    verified = [r["proof_verified"] for r in rows if r["proof_verified"] is not None]
    if verified and all(verified):
        score += 40
        reasons.append("all proofs verified against on-chain root")
    elif verified:
        score += 0
        reasons.append("PROOF MISMATCH")
    if any(r["status"] == ERROR for r in rows):
        score -= 20
        reasons.append("claim checks errored")
    if payload.get("claim_deadline") and _parse_deadline(payload["claim_deadline"]):
        score += 10
        reasons.append("deadline documented")
    if payload.get("verified_on"):
        score += 10
        reasons.append(f"registry entry verified {payload['verified_on']}")
    score = max(0, min(100, score))
    label = "high" if score >= 70 else ("medium" if score >= 40 else "low")
    return {"score": score, "label": label, "reasons": reasons}


def _report_from_dict(payload: dict) -> ScanReport:
    from .scanner import ClaimCheck, Holding

    report = ScanReport(
        wallets=payload.get("wallets", []),
        chains=payload.get("chains", []),
        warnings=payload.get("warnings", []),
        generated_at=payload.get("generated_at", ""),
    )
    for item in payload.get("holdings", []):
        report.holdings.append(
            Holding(
                chain=item["chain"],
                chain_id=int(item.get("chain_id", 1)),
                wallet=item["wallet"],
                symbol=item["asset"],
                asset_address=item.get("asset_address"),
                decimals=int(item["decimals"]),
                raw=int(item["raw"]),
                usd_price=item.get("usd_price"),
            )
        )
    for item in payload.get("claims", []):
        report.claims.append(
            ClaimCheck(
                drop_id=item["drop_id"],
                name=item["name"],
                chain=item["chain"],
                chain_id=int(item.get("chain_id", 1)),
                wallet=item["wallet"],
                distributor=item["distributor"],
                status=item["status"],
                symbol=item.get("token") or "?",
                token=item.get("token"),
                decimals=int(item.get("decimals", 18)),
                amount_raw=int(item.get("amount_raw", 0)),
                usd_price=item.get("usd_price"),
                gas_native=item.get("gas_native"),
                gas_usd=item.get("gas_usd"),
                proof_verified=item.get("proof_verified"),
                source=item.get("eligibility_source"),
                note=item.get("note"),
                deadline=item.get("deadline"),
                expired=bool(item.get("expired")),
            )
        )
    return report
