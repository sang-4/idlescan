"""Report rendering and export formats."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from idlescan.gas import GAS_ESTIMATE_NOTE
from idlescan.report import DISCLAIMER, export, render_console, render_markdown, to_dict
from idlescan.scanner import CLAIMED
from idlescan.selftest import run_selftest


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_selftest()

    def test_console_contains_sections(self):
        text = render_console(self.report)
        for expected in ("IDLE BALANCES", "AIRDROPS", "TOTALS", "unclaimed", "claimed"):
            self.assertIn(expected, text)

    def test_console_hides_no_allocation_by_default(self):
        self.assertNotIn("no_allocation", render_console(self.report))
        self.assertIn("no_allocation", render_console(self.report, show_empty=True))

    def test_dict_is_json_serialisable(self):
        payload = to_dict(self.report)
        text = json.dumps(payload)
        self.assertIn("holdings", text)
        again = json.loads(text)
        self.assertEqual(len(again["claims"]), len(self.report.claims))
        self.assertIsNotNone(again["totals"])
        self.assertIn("unclaimed_usd", again["totals"])

    def test_exports_write_expected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "report"
            written = export(self.report, base, ["json", "csv", "md"])
            self.assertEqual(len(written), 3)
            for path in written:
                self.assertTrue(path.exists(), path)

            payload = json.loads((Path(tmp) / "report.json").read_text())
            self.assertEqual(payload["wallets"], self.report.wallets)

            rows = list(csv.reader((Path(tmp) / "report.csv").read_text().splitlines()))
            self.assertEqual(rows[0][0], "kind")
            # Every holding + claim, plus one report-level metadata row.
            self.assertEqual(len(rows) - 1, len(self.report.holdings) + len(self.report.claims) + 1)

            markdown = (Path(tmp) / "report.md").read_text()
            self.assertIn("# Idle asset & unclaimed airdrop report", markdown)
            self.assertIn("## Totals", markdown)

    def test_single_format_keeps_given_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom.md"
            written = export(self.report, target, ["md"])
            self.assertEqual(written[0], target)
            self.assertTrue(target.exists())

    def test_gas_estimate_note_is_report_level_in_json(self):
        payload = to_dict(self.report)
        self.assertEqual(payload["gas_estimate_note"], GAS_ESTIMATE_NOTE)
        for claim in payload["claims"]:
            self.assertNotIn("gas_estimate_note", claim)

    def test_gas_estimate_note_in_console_markdown_and_csv(self):
        self.assertIn(GAS_ESTIMATE_NOTE, render_console(self.report))
        self.assertIn(GAS_ESTIMATE_NOTE, render_markdown(self.report))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.csv"
            export(self.report, target, ["csv"])
            rows = list(csv.reader(target.read_text().splitlines()))
        trailers = [row for row in rows if row and row[0] == "note"]
        self.assertEqual(len(trailers), 1)
        self.assertEqual(trailers[0][3], "gas_estimate_note")
        self.assertEqual(trailers[0][12], GAS_ESTIMATE_NOTE)

    def test_disclaimer_in_console_and_markdown(self):
        self.assertIn(DISCLAIMER, render_console(self.report))
        self.assertIn(DISCLAIMER, render_markdown(self.report))

    def test_markdown_shows_proof_state(self):
        markdown = render_markdown(self.report)
        self.assertIn("verified", markdown)

    def test_totals_are_none_without_prices(self):
        # The selftest runs with pricing disabled, so we must not invent $0.00.
        self.assertIsNone(self.report.idle_value)
        self.assertIsNone(self.report.total_value)
        self.assertIn("combined        -", render_console(self.report))

    def test_unclaimed_claim_includes_calldata(self):
        payload = to_dict(self.report)
        unclaimed = [c for c in payload["claims"] if c["status"] == "unclaimed"]
        self.assertTrue(unclaimed)
        self.assertTrue(unclaimed[0]["claim_calldata"].startswith("0x"))

    def test_claimed_rows_hide_value(self):
        claimed = [c for c in self.report.claims if c.status == CLAIMED]
        self.assertTrue(claimed)
        self.assertIsNone(claimed[0].net_usd)


if __name__ == "__main__":
    unittest.main()
