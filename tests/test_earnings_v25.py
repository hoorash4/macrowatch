from __future__ import annotations

import unittest
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

from earnings_v25.diagnose_structured import parser, relevant_accounts
from earnings_v25.pipeline import KoreaEarningsV2Pipeline
from earnings_v25.raw_dart_financials import parse_raw_filing_archive


def archive(document: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zipped:
        zipped.writestr("report.xml", document.encode("utf-8"))
    return buffer.getvalue()


class EarningsV25DiagnosticTests(unittest.TestCase):
    def test_requires_supported_quarter(self) -> None:
        args = parser().parse_args(["--year", "2016", "--quarter", "1"])
        self.assertEqual((args.year, args.quarter), (2016, 1))

    def test_reports_historical_account_names_and_ignores_balance_sheet(self) -> None:
        rows = [
            {"sj_div": "IS", "account_id": "custom_OperatingProfit", "account_nm": "영업이익(손실)", "thstrm_amount": "10"},
            {"sj_div": "CIS", "account_id": "custom_ParentProfit", "account_nm": "지배기업소유주지분순이익", "thstrm_amount": "8"},
            {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계", "thstrm_amount": "20"},
        ]

        result = relevant_accounts(rows)

        self.assertEqual([row["account_nm"] for row in result], [
            "영업이익(손실)", "지배기업소유주지분순이익",
        ])

    def test_raw_parser_reads_top_line_and_prefers_consolidated_scope(self) -> None:
        document = """
        <P>연결손익계산서</P><P>2016년 1월 1일부터 2016년 3월 31일까지</P>
        <P>(단위 : 백만원)</P><TABLE>
          <TR><TH>과목</TH><TH>당분기</TH><TH>전분기</TH></TR>
          <TR><TD>영업수익</TD><TD>7,924,894</TD><TD>1</TD></TR>
          <TR><TD>영업이익</TD><TD>560,387</TD><TD>1</TD></TR>
          <TR><TD>분(당)기순이익</TD><TD>1,268,273</TD><TD>1</TD></TR>
        </TABLE>
        """
        parsed = parse_raw_filing_archive(
            archive(document), report_code="11013", fiscal_year=2016,
        )
        self.assertEqual(parsed["CFS"].cumulative["top_line"], Decimal("7924894000000"))
        self.assertEqual(parsed["CFS"].cumulative["operating_income"], Decimal("560387000000"))
        self.assertEqual(parsed["CFS"].cumulative["net_income"], Decimal("1268273000000"))

    def test_raw_quarter_rules_use_reported_interim_and_q4_average_fallback(self) -> None:
        statement = type("Statement", (), {
            "cumulative": {"top_line": Decimal("120"), "operating_income": None, "net_income": None},
            "standalone": {"top_line": Decimal("50"), "operating_income": None, "net_income": None},
        })()
        self.assertEqual(
            KoreaEarningsV2Pipeline._raw_standalone_value(statement, "top_line", 2, None),
            Decimal("50"),
        )
        self.assertEqual(
            KoreaEarningsV2Pipeline._raw_standalone_value(statement, "top_line", 4, None),
            Decimal("30"),
        )

    def test_raw_parser_strips_only_presentation_notes_from_exact_accounts(self) -> None:
        document = """
        <P>연결손익계산서</P><P>2016년 1월 1일부터 2016년 3월 31일까지</P>
        <P>(단위 : 원)</P><TABLE>
          <TR><TH>과목</TH><TH>당분기</TH><TH>전분기</TH></TR>
          <TR><TD>영업수익&lt;주석39&gt;</TD><TD>100</TD><TD>90</TD></TR>
          <TR><TD>영업이익</TD><TD>10</TD><TD>9</TD></TR>
          <TR><TD>분기순이익&amp;cr; (대손준비금 반영후 조정이익)</TD><TD>8</TD><TD>7</TD></TR>
        </TABLE>
        """
        parsed = parse_raw_filing_archive(
            archive(document), report_code="11013", fiscal_year=2016,
        )
        self.assertEqual(parsed["CFS"].cumulative["top_line"], Decimal("100"))
        self.assertEqual(parsed["CFS"].cumulative["net_income"], Decimal("8"))

    def test_raw_parser_accepts_published_mixed_period_and_connected_net_income(self) -> None:
        document = """
        <P>연결손익계산서</P><P>2016년 1월 1일부터 2016년 3월 31일까지</P>
        <P>(단위 : 백만원)</P><TABLE>
          <TR><TH>과목</TH><TH>당분기</TH><TH>전분기</TH></TR>
          <TR><TD>영업수익</TD><TD>100</TD><TD>90</TD></TR>
          <TR><TD>영업이익</TD><TD>10</TD><TD>9</TD></TR>
          <TR><TD>당(분)기순이익</TD><TD>8</TD><TD>7</TD></TR>
        </TABLE>
        """
        parsed = parse_raw_filing_archive(
            archive(document), report_code="11013", fiscal_year=2016,
        )
        self.assertEqual(parsed["CFS"].cumulative["net_income"], Decimal("8000000"))

        connected = document.replace("당(분)기순이익", "당기연결순이익")
        parsed = parse_raw_filing_archive(
            archive(connected), report_code="11013", fiscal_year=2016,
        )
        self.assertEqual(parsed["CFS"].cumulative["net_income"], Decimal("8000000"))


if __name__ == "__main__":
    unittest.main()

