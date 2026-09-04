import unittest
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from test_earnings_v2 import fact, member
from earnings_v2.financial_company import FinancialCompanyClient, merge_financial_company
from earnings_v2.http import ExecutionDeadlineExceeded
from earnings_v2.pipeline import KoreaEarningsV2Pipeline
from earnings_v2.providers import ProviderError
from earnings_v25.providers import FinancialCompanySnapshot, FINANCIAL_SECTOR_SPECS, REPORT_CODES


class FscBackfillTests(unittest.TestCase):
    def snapshot(self, quarter=2, **changes):
        return replace(FinancialCompanySnapshot(
            crno="1234567890123", report_code=REPORT_CODES[quarter],
            consolidation_scope="CFS", currency="KRW",
            top_line_cumulative=Decimal(100), operating_income_cumulative=Decimal(20),
            net_income_cumulative=Decimal(10)), **changes)

    def test_preserve_existing_zero_and_use_reported_quarter(self):
        original = fact(2025, 2, "3").with_changes(top_line=Decimal(0), net_income=None)
        result = merge_financial_company(original, [self.snapshot(net_income_standalone=Decimal(8))], 2025, 2, None)
        self.assertEqual((result.top_line, result.operating_income, result.net_income), (0, 3, 8))
        self.assertEqual(result.source_net_income_cumulative, 10)
        self.assertFalse(result.is_pending)

    def test_cumulative_subtraction_or_backfill_average_each_quarter(self):
        for quarter in range(1, 5):
            with self.subTest(quarter=quarter):
                original = fact(2025, quarter, "3").with_changes(net_income=None)
                prior = fact(2025, max(1, quarter-1), "3").with_changes(
                    source_currency="KRW", source_net_income_cumulative=Decimal(4))
                result = merge_financial_company(original, [self.snapshot(quarter)], 2025, quarter, prior)
                self.assertEqual(result.net_income, 10 if quarter == 1 else 6)
                result = merge_financial_company(original, [self.snapshot(quarter)], 2025, quarter, None)
                self.assertEqual(result.net_income, Decimal(10) / quarter)

    def test_missing_and_incompatible_data_stay_pending(self):
        original = fact(2025, 2, "3").with_changes(net_income=None, is_pending=True)
        for snapshot in (self.snapshot(net_income_cumulative=None),
                         self.snapshot(consolidation_scope="OFS"), self.snapshot(currency="USD"),
                         self.snapshot(report_code=REPORT_CODES[3])):
            self.assertIs(merge_financial_company(original, [snapshot], 2025, 2, None), original)

    def test_order_and_early_completion(self):
        for complete_at in ("existing", "dart", "kis", "fsc"):
            with self.subTest(complete_at=complete_at):
                calls = []
                original = fact(2025, 2, "3")
                incomplete = original.with_changes(net_income=None, is_pending=True)
                pipeline = KoreaEarningsV2Pipeline(
                    krx=None, repository=None, kis=object(),
                    dart=SimpleNamespace(company_profile=lambda _: {"jurir_no": "1234567890123"}),
                    financial_company=SimpleNamespace(quarter_financials=lambda *args: calls.append("fsc") or [self.snapshot()]))
                pipeline._single_open_dart_missing_financials = lambda *args: calls.append("dart") or (original if complete_at == "dart" else incomplete)
                pipeline._try_kis_missing_financials = lambda *args, **kwargs: (calls.append("kis") or (original if complete_at == "kis" else incomplete), None)
                identity = replace(member("kr:1", 1), industry_code="64992")
                resolved, issue = pipeline._resolve_missing_financials(identity, original if complete_at == "existing" else incomplete, 2025, 2)
                self.assertEqual(calls, {"existing": [], "dart": ["dart"], "kis": ["dart", "kis"], "fsc": ["dart", "kis", "fsc"]}[complete_at])
                self.assertTrue(resolved.fully_complete)
                self.assertIsNone(issue)

    def test_six_sector_routes_and_common_seventh_service(self):
        client = FinancialCompanyClient("https://example.test", "service", "token", "key")
        calls = []
        client._sector_quarter_financials = lambda crno, year, quarter, sector: calls.append(sector) or [self.snapshot()]
        for sector, spec in FINANCIAL_SECTOR_SPECS.items():
            client.quarter_financials("1234567890123", 2025, 2, spec["industry_prefixes"][0])
        self.assertEqual(set(calls), {"bank", "holding", "life", "nonlife", "card", "securities"})
        client._sector_quarter_financials = lambda *args: []
        client._source_request = lambda payload, **kwargs: calls.append("common") or {"status": "no_report"}
        self.assertEqual(client.quarter_financials("1234567890123", 2025, 2, "641"), [])
        self.assertEqual(calls[-1], "common")

    @patch("earnings_v2.financial_company.bounded_request", side_effect=ExecutionDeadlineExceeded("deadline"))
    def test_deadline_is_not_swallowed(self, _request):
        client = FinancialCompanyClient("https://example.test", "service", "token", "key")
        with self.assertRaises(ExecutionDeadlineExceeded):
            client._source_request({}, operation="test")

    def test_wrong_company_rejected(self):
        client = FinancialCompanyClient("https://example.test", "service", "token", "key")
        client._sector_quarter_financials = lambda *args: [self.snapshot(crno="9999999999999")]
        with self.assertRaises(ProviderError):
            client.quarter_financials("1234567890123", 2025, 2, "641")

