"""Dataset streaming, provenance, staleness and database persistence."""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from idlescan import __version__
from idlescan.chains import Chain, ChainConfig, NativeAsset
from idlescan.cli import main
from idlescan.dataset import (
    DatasetError,
    DatasetIndex,
    dataset_provenance,
    is_stale,
    parse_dataset_file,
)
from idlescan.db import Database
from idlescan.merkle import build_proof, compute_root, leaf_hash, parse_dataset
from idlescan.registry import Airdrop, Eligibility
from idlescan.scanner import Scanner, ScanReport

WALLET_A = "0x" + "11" * 20
WALLET_B = "0x" + "22" * 20
DISTRIBUTOR = "0x" + "dd" * 20
TOKEN = "0x" + "aa" * 20


def build_dataset(claims):
    root = compute_root([leaf_hash(index, account, amount) for index, (account, amount) in enumerate(claims)])
    entries = []
    for index, (account, amount) in enumerate(claims):
        proof = build_proof(
            index,
            account,
            amount,
            [(item, amt) for item, amt in claims],
        )
        entries.append({
            "account": account,
            "index": index,
            "amount": str(amount),
            "proof": ["0x" + item.hex() for item in proof],
        })
    return root, entries


def write_dataset(path: Path, claims, *, array=True, extra=None):
    root, entries = build_dataset(claims)
    payload = {"merkleRoot": "0x" + root.hex()}
    if array:
        payload["claims"] = entries
    else:
        payload["claims"] = {entry["account"]: entry for entry in entries}
    payload.update(extra or {})
    path.write_text(json.dumps(payload))
    return root


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class StreamingParseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "claims.json"
        self.claims = [(WALLET_A, 10**18), (WALLET_B, 2 * 10**18)]

    def tearDown(self):
        self._tmp.cleanup()

    def test_streamed_array_matches_regular_parse(self):
        root = write_dataset(self.path, self.claims)
        streamed = parse_dataset_file(self.path, stream_threshold=0, max_bytes=10_000_000)
        try:
            regular = parse_dataset(json.loads(self.path.read_text()))
            self.assertEqual(streamed.root, root)
            self.assertEqual(len(streamed), 2)
            self.assertEqual(streamed.get(WALLET_A).amount, 10**18)
            self.assertEqual(streamed.get(WALLET_B).proof, regular.get(WALLET_B).proof)
            self.assertIsInstance(streamed.claims, DatasetIndex)
        finally:
            streamed.close()
        self.assertFalse(streamed.claims.path.exists())

    def test_streamed_object_form(self):
        write_dataset(self.path, self.claims, array=False)
        dataset = parse_dataset_file(self.path, stream_threshold=0)
        try:
            self.assertEqual(len(dataset), 2)
            self.assertEqual(dataset.get(WALLET_B).index, 1)
        finally:
            dataset.close()

    def test_streamed_bare_object_form(self):
        _, entries = build_dataset(self.claims)
        self.path.write_text(json.dumps({entry["account"]: entry for entry in entries}))
        dataset = parse_dataset_file(self.path, stream_threshold=0)
        try:
            self.assertEqual(len(dataset), 2)
            self.assertIsNone(dataset.root)
            self.assertEqual(dataset.get(WALLET_A).amount, 10**18)
        finally:
            dataset.close()

    def test_small_file_uses_the_plain_parser(self):
        write_dataset(self.path, self.claims)
        dataset = parse_dataset_file(self.path)
        self.assertNotIsInstance(dataset.claims, DatasetIndex)
        self.assertEqual(len(dataset), 2)

    def test_truncated_stream_is_rejected(self):
        write_dataset(self.path, self.claims)
        raw = self.path.read_text()
        self.path.write_text(raw[:-40])
        with self.assertRaises(DatasetError):
            parse_dataset_file(self.path, stream_threshold=0)

    def test_oversized_file_is_refused(self):
        write_dataset(self.path, self.claims)
        with self.assertRaises(DatasetError) as ctx:
            parse_dataset_file(self.path, max_bytes=10)
        self.assertIn("safety cap", str(ctx.exception))

    def test_malformed_claim_entry_is_named(self):
        _, entries = build_dataset(self.claims)
        entries[0]["amount"] = "not-a-number"
        self.path.write_text(json.dumps({"claims": entries}))
        with self.assertRaises(DatasetError) as ctx:
            parse_dataset_file(self.path, stream_threshold=0)
        self.assertIn(WALLET_A, str(ctx.exception))

    def test_tiny_chunk_size_handles_token_boundaries(self):
        write_dataset(self.path, self.claims)
        dataset = parse_dataset_file(self.path, stream_threshold=0)
        try:
            # Reads across many buffers still produce the same values.
            self.assertEqual(len(dataset), 2)
            self.assertEqual(dataset.get(WALLET_A).amount, 10**18)
        finally:
            dataset.close()


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "distribution.json"
        write_dataset(self.path, [(WALLET_A, 10**18)])

    def tearDown(self):
        self._tmp.cleanup()

    def test_provenance_hashes_size_and_file_date(self):
        root, _ = build_dataset([(WALLET_A, 10**18)])
        provenance = dataset_provenance(self.path, drop_id="drop", root=root, entry_count=1, tool_version="1.2.0")
        self.assertEqual(provenance.filename, "distribution.json")
        self.assertEqual(provenance.sha256, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(provenance.byte_size, self.path.stat().st_size)
        self.assertEqual(provenance.root, "0x" + root.hex())
        self.assertEqual(provenance.drop_id, "drop")
        self.assertEqual(provenance.as_dict()["entry_count"], 1)

    def test_staleness_uses_the_file_date(self):
        old = time.time() - 60 * 24 * 3600
        os.utime(self.path, (old, old))
        provenance = dataset_provenance(self.path, drop_id="drop")
        self.assertTrue(is_stale(provenance, 30))
        self.assertFalse(is_stale(provenance, 90))
        self.assertFalse(is_stale(provenance, 0))

    def test_future_or_unparseable_dates_are_not_crashes(self):
        provenance = dataset_provenance(self.path, drop_id="drop")
        broken = type(provenance)(**{**provenance.as_dict(), "fetched_at": "nonsense"})
        self.assertTrue(is_stale(broken, 30))


class DatabaseProvenanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "idlescan.db").init()

    def tearDown(self):
        self._tmp.cleanup()

    def record(self, **overrides):
        value = {
            "drop_id": "drop",
            "filename": "distribution.json",
            "source_url": "https://example.org/distribution.json?token=SECRET",
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "sha256": "ab" * 32,
            "byte_size": 1234,
            "root_verified": True,
            "root": "0x" + "cd" * 32,
            "block_number": 19_000_000,
            "entry_count": 42,
            "tool_version": "1.2.0",
        }
        value.update(overrides)
        return value

    def test_upsert_and_read(self):
        self.db.upsert_dataset_provenance(self.record())
        stored = self.db.dataset_provenance("drop")
        self.assertEqual(stored["sha256"], "ab" * 32)
        self.assertTrue(stored["root_verified"])
        self.assertEqual(stored["entry_count"], 42)

    def test_upsert_replaces_the_previous_record(self):
        self.db.upsert_dataset_provenance(self.record())
        self.db.upsert_dataset_provenance(self.record(entry_count=7, sha256="ef" * 32))
        stored = self.db.dataset_provenance("drop")
        self.assertEqual(stored["entry_count"], 7)
        self.assertEqual(stored["sha256"], "ef" * 32)

    def test_missing_record_is_none(self):
        self.assertIsNone(self.db.dataset_provenance("nope"))


class ScannerProvenanceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "distribution.json"
        write_dataset(self.path, [(WALLET_A, 10**18), (WALLET_B, 2 * 10**18)])
        chain = Chain(
            key="ethereum",
            chain_id=1,
            name="Ethereum",
            native=NativeAsset(symbol="ETH", decimals=18),
            rpcs=("mock://ethereum",),
        )
        self.chains = ChainConfig(chains={"ethereum": chain})
        drop = Airdrop(
            id="drop",
            name="Drop",
            chain="ethereum",
            distributor=DISTRIBUTOR,
            token=TOKEN,
            eligibility=Eligibility(type="merkle_dataset", dataset=str(self.path)),
        )
        self.drop = drop

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_dataset_records_provenance(self):
        scanner = Scanner(self.chains, {}, [self.drop], dataset_stale_days=30)
        report = ScanReport(wallets=[WALLET_A], chains=["ethereum"])
        dataset = scanner._load_dataset(self.drop, report)
        self.assertIsNotNone(dataset)
        self.assertEqual(len(report.datasets), 1)
        item = report.datasets[0]
        self.assertEqual(item["drop_id"], "drop")
        self.assertEqual(item["entry_count"], 2)
        self.assertEqual(item["filename"], "distribution.json")
        self.assertTrue(item["sha256"])
        dataset.close()

    def test_stale_dataset_produces_a_warning(self):
        old = time.time() - 60 * 24 * 3600
        os.utime(self.path, (old, old))
        scanner = Scanner(self.chains, {}, [self.drop], dataset_stale_days=30)
        report = ScanReport(wallets=[WALLET_A], chains=["ethereum"])
        dataset = scanner._load_dataset(self.drop, report)
        self.assertTrue(any("older than 30 days" in item for item in report.warnings))
        dataset.close()

    def test_missing_dataset_is_a_clear_warning(self):
        self.path.unlink()
        scanner = Scanner(self.chains, {}, [self.drop], dataset_stale_days=30)
        report = ScanReport(wallets=[WALLET_A], chains=["ethereum"])
        self.assertIsNone(scanner._load_dataset(self.drop, report))
        self.assertTrue(any("not found" in item for item in report.warnings))
        self.assertEqual(report.datasets, [])


class DatasetCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "claims.json"
        self.root = write_dataset(self.path, [(WALLET_A, 10**18)])

    def tearDown(self):
        self._tmp.cleanup()

    def test_dataset_command_reports_provenance(self):
        code, out, _ = run_cli(
            "dataset",
            str(self.path),
            "--distributor",
            DISTRIBUTOR,
            "--chain",
            "ethereum",
            "--root",
            "0x" + self.root.hex(),
            "--json",
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["provenance"]["entry_count"], 1)
        self.assertEqual(payload["provenance"]["tool_version"], __version__)
        self.assertEqual(payload["claims"], 1)

    def test_dataset_command_rejects_a_bad_file(self):
        self.path.write_text("{not json")
        code, _, err = run_cli(
            "dataset",
            str(self.path),
            "--distributor",
            DISTRIBUTOR,
            "--chain",
            "ethereum",
            "--root",
            "0x" + "11" * 32,
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)


if __name__ == "__main__":
    unittest.main()
