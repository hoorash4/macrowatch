from __future__ import annotations

import unittest
from decimal import Decimal

from earnings_v25.providers import FinancialCompanyClient


class FinancialSectorIncomeStatementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FinancialCompanyClient(
            "https://example.supabase.co", "service", "internal", "public",
            session=object(),
        )
        self.crno = "1101110012345"
        self.base_month = "201809"

    def snapshot(self, sector: str, rows: list[dict[str, str]]):
        self.client._sector_rows_cache[(sector, self.base_month)] = [
            {
                "basYm": self.base_month,
                "crno": self.crno,
                **row,
            }
            for row in rows
        ]
        snapshots = self.client._sector_quarter_financials(
            self.crno, 2018, 3, sector,
        )
        self.assertEqual(len(snapshots), 1)
        return snapshots[0]

    def test_industry_codes_route_to_all_six_sector_apis(self) -> None:
        expected = {
            "64121": "bank",
            "64992": "holding",
            "65110": "life",
            "65121": "nonlife",
            "64913": "card",
            "66121": "securities",
        }
        for industry_code, sector in expected.items():
            with self.subTest(industry_code=industry_code):
                self.assertEqual(
                    self.client._sector_for_industry(industry_code), sector,
                )

    def test_bank_uses_reported_cumulative_and_quarter_values(self) -> None:
        rows = [
            {"bnkSmryPlSbjCdNm": "영업수익", "bnkSmryPlSbjCmtlAt": "100", "bnkSmryPlSbjThqrAmt": "40"},
            {"bnkSmryPlSbjCdNm": "영업이익", "bnkSmryPlSbjCmtlAt": "20", "bnkSmryPlSbjThqrAmt": "8"},
            {"bnkSmryPlSbjCdNm": "당기순이익", "bnkSmryPlSbjCmtlAt": "15", "bnkSmryPlSbjThqrAmt": "6"},
        ]
        result = self.snapshot("bank", rows)
        self.assertEqual(result.top_line_cumulative, Decimal("100"))
        self.assertEqual(result.operating_income_standalone, Decimal("8"))
        self.assertEqual(result.net_income_standalone, Decimal("6"))

    def test_life_top_line_sums_only_non_overlapping_parent_revenues(self) -> None:
        rows = [
            {"smryPlAcitCdNm": "보험손익_보험영업수익", "smryPlAcitCmtlAmt": "100", "smryPlAcitThqrAmt": "30"},
            {"smryPlAcitCdNm": "투자손익_투자영업수익", "smryPlAcitCmtlAmt": "40", "smryPlAcitThqrAmt": "12"},
            {"smryPlAcitCdNm": "특별계정손익_특별계정수익", "smryPlAcitCmtlAmt": "10", "smryPlAcitThqrAmt": "3"},
            {"smryPlAcitCdNm": "보험손익_보험영업수익_보험료수익", "smryPlAcitCmtlAmt": "90", "smryPlAcitThqrAmt": "27"},
            {"smryPlAcitCdNm": "영업이익", "smryPlAcitCmtlAmt": "20", "smryPlAcitThqrAmt": "7"},
            {"smryPlAcitCdNm": "당기순이익", "smryPlAcitCmtlAmt": "15", "smryPlAcitThqrAmt": "5"},
        ]
        result = self.snapshot("life", rows)
        self.assertEqual(result.top_line_cumulative, Decimal("150"))
        self.assertEqual(result.top_line_standalone, Decimal("45"))

    def test_nonlife_top_line_uses_three_parent_revenue_rows(self) -> None:
        rows = [
            {"smryPlAcitCdNm": "경과보험료", "smryPlAcitCmtlAmt": "100", "smryPlAcitThqrAmt": "35"},
            {"smryPlAcitCdNm": "투자영업수익", "smryPlAcitCmtlAmt": "30", "smryPlAcitThqrAmt": "9"},
            {"smryPlAcitCdNm": "특별계정이익_특별계정수익", "smryPlAcitCmtlAmt": "5", "smryPlAcitThqrAmt": "1"},
            {"smryPlAcitCdNm": "총영업이익", "smryPlAcitCmtlAmt": "18", "smryPlAcitThqrAmt": "6"},
            {"smryPlAcitCdNm": "당기순이익(또는 당기순손실)", "smryPlAcitCmtlAmt": "12", "smryPlAcitThqrAmt": "4"},
        ]
        result = self.snapshot("nonlife", rows)
        self.assertEqual(result.top_line_cumulative, Decimal("135"))
        self.assertEqual(result.operating_income_standalone, Decimal("6"))
        self.assertEqual(result.net_income_cumulative, Decimal("12"))

    def test_direct_total_sectors_do_not_sum_child_accounts(self) -> None:
        cases = {
            "holding": ("smryLnkPlAcitCdNm", "smryLnkPlAcitCmtlAmt", "smryLnkPlAcitAmt", "영업수익", "영업이익", "연결당기순이익"),
            "card": ("smryPlAcitCdNm", "smryPlAcitCmtlAmt", "smryPlAcitThqrAmt", "영업수익", "영업이익", "당기순이익(손실)"),
            "securities": ("smryPlAcitCdNm", "cmtlAmt", "thqrAmt", "[영업수익]", "[영업이익(손실)]", "[당기순이익(손실)]"),
        }
        for sector, (name, cumulative, quarter, top, operating, net) in cases.items():
            with self.subTest(sector=sector):
                rows = [
                    {name: top, cumulative: "100", quarter: "40"},
                    {name: f"{top}_하위계정", cumulative: "90", quarter: "30"},
                    {name: operating, cumulative: "20", quarter: "8"},
                    {name: net, cumulative: "15", quarter: "6"},
                ]
                result = self.snapshot(sector, rows)
                self.assertEqual(result.top_line_cumulative, Decimal("100"))
                self.assertEqual(result.top_line_standalone, Decimal("40"))


if __name__ == "__main__":
    unittest.main()
