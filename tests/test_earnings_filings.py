from datetime import date
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.discover_filings import canonical_payload_hash, discovery_window  # noqa: E402
from earnings.filings import parse_periodic_filings  # noqa: E402


class EarningsFilingDiscoveryTests(unittest.TestCase):
    def test_regular_and_corrected_periodic_filings_are_preserved(self) -> None:
        rows = [
            {
                "corp_code": "00126380",
                "rcept_no": "20260828000001",
                "rcept_dt": "20260828",
                "report_nm": "반기보고서 (2026.06)",
            },
            {
                "corp_code": "00126380",
                "rcept_no": "20260828000002",
                "rcept_dt": "20260828",
                "report_nm": "[기재정정]반기보고서 (2026.06)",
            },
            {
                "corp_code": "00126380",
                "rcept_no": "20260828000003",
                "rcept_dt": "20260828",
                "report_nm": "주요사항보고서",
            },
        ]
        filings = parse_periodic_filings(rows)
        self.assertEqual(len(filings), 2)
        self.assertEqual(filings[0].report_code, "11012")
        self.assertFalse(filings[0].is_correction)
        self.assertTrue(filings[1].is_correction)

    def test_checkpoint_window_overlaps_three_weekdays(self) -> None:
        begin, end = discovery_window(
            date(2026, 8, 31),
            {"cursor": {"through_date": "2026-08-28"}},
        )
        self.assertEqual(begin, date(2026, 8, 25))
        self.assertEqual(end, date(2026, 8, 31))

    def test_first_run_has_long_holiday_safe_lookback(self) -> None:
        begin, _ = discovery_window(date(2026, 8, 28), None)
        self.assertEqual(begin, date(2026, 8, 14))

    def test_payload_hash_is_stable_across_key_order(self) -> None:
        self.assertEqual(canonical_payload_hash({"a": 1, "b": 2}), canonical_payload_hash({"b": 2, "a": 1}))


if __name__ == "__main__":
    unittest.main()
