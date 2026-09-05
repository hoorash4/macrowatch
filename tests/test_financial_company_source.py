from __future__ import annotations

from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import patch

from earnings_v2.models import CompanyIdentity, FinancialFact
from earnings_v25.pipeline import KoreaEarningsV2Pipeline
from earnings_v25.providers import FinancialCompanyClient, FinancialCompanySnapshot


class StubFinancialCompany:
    def __init__(self, snapshots: list[FinancialCompanySnapshot]) -> None:
        self.snapshots = snapshots
        self.request_count = 0

    def quarter_financials(self, _company_name: str, _year: int, _quarter: int) -> list[FinancialCompanySnapshot]:
        self.request_count += 1
        return self.snapshots


def identity() -> CompanyIdentity:
    return CompanyIdentity(
        company_id="company", company_name="테스트금융", stock_code="000000",
        corp_code="00000000", market_id="kr_largecap", rank=1,
        market_cap=Decimal("1"), reference_date=date(2018, 9, 28),
        entity_kind="financial",
    )


def fact(*, top_line: Decimal | None = None, scope: str = "CFS") -> FinancialFact:
    return FinancialFact(
        company_id="company", fiscal_year=2018, fiscal_quarter=3,
        period_end=date(2018, 9, 30), top_line=top_line,
        operating_income=None, net_income=None, currency="KRW",
        consolidation_scope=scope, source_filing_id="open_dart:test",
        filing_date=date(2018, 11, 14),
    )


class FinancialCompanySupplementTests(unittest.TestCase):
    def test_fills_only_missing_metrics_and_subtracts_previous_cumulative(self) -> None:
        client = StubFinancialCompany([FinancialCompanySnapshot(
            crno="1234567890123", report_code="11014", consolidation_scope="CFS",
            currency="KRW", top_line_cumulative=Decimal("300"),
            operating_income_cumulative=Decimal("90"), net_income_cumulative=Decimal("60"),
        )])
        pipeline = KoreaEarningsV2Pipeline(krx=None, dart=None, repository=None, financial_company=client)
        previous = FinancialFact(
            company_id="company", fiscal_year=2018, fiscal_quarter=2,
            period_end=date(2018, 6, 30), top_line=Decimal("100"),
            operating_income=Decimal("30"), net_income=Decimal("20"), currency="KRW",
            consolidation_scope="CFS", source_filing_id="stored", filing_date=date(2018, 8, 14),
            source_top_line_cumulative=Decimal("200"),
            source_operating_income_cumulative=Decimal("60"),
            source_net_income_cumulative=Decimal("40"),
        )
        resolved = pipeline._financial_company_missing_financials(identity(), fact(), 2018, 3, previous, "1234567890123")
        self.assertEqual(resolved.top_line, Decimal("100"))
        self.assertEqual(resolved.operating_income, Decimal("30"))
        self.assertEqual(resolved.net_income, Decimal("20"))
        self.assertEqual(resolved.source, "financial_services_commission")
        self.assertEqual(client.request_count, 1)

    def test_other_scope_fills_only_missing_metrics_in_partial_fact(self) -> None:
        client = StubFinancialCompany([FinancialCompanySnapshot(
            crno="1234567890123", report_code="11014", consolidation_scope="OFS",
            currency="KRW", top_line_cumulative=Decimal("300"),
            operating_income_cumulative=Decimal("90"), net_income_cumulative=Decimal("60"),
        )])
        pipeline = KoreaEarningsV2Pipeline(krx=None, dart=None, repository=None, financial_company=client)
        resolved = pipeline._financial_company_missing_financials(
            identity(), fact(top_line=Decimal("101"), scope="CFS"), 2018, 3, None, "1234567890123",
        )
        self.assertEqual(resolved.top_line, Decimal("101"))
        self.assertEqual(resolved.operating_income, Decimal("30"))
        self.assertEqual(resolved.net_income, Decimal("20"))
        self.assertEqual(resolved.consolidation_scope, "CFS")
        self.assertEqual(resolved.source, "mixed")
        self.assertFalse(resolved.is_pending)

    def test_passes_github_public_data_key_only_to_the_protected_proxy(self) -> None:
        client = FinancialCompanyClient(
            "https://example.supabase.co",
            "service-role-key",
            "internal-token",
            "data-go-key",
            session=object(),
        )
        with patch("earnings_v25.providers.bounded_request", return_value={
            "status": "no_report", "crno": "1234567890123",
        }) as request:
            self.assertEqual(client.quarter_financials("1234567890123", 2018, 3), [])
        self.assertEqual(
            request.call_args.kwargs["headers"]["X-Public-Data-API-Key"],
            "data-go-key",
        )
        self.assertEqual(
            request.call_args.kwargs["json"],
            {"crno": "1234567890123", "fiscal_year": 2018},
        )


if __name__ == "__main__":
    unittest.main()
