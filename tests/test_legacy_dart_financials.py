from io import BytesIO
import unittest
from zipfile import ZipFile

from earnings.legacy_dart_financials import (
    LegacyCumulativeStatement,
    LegacyDartParseError,
    parse_legacy_filing_archive,
)
from earnings.collect_legacy_financials import build_legacy_standalone_quarters
from earnings.collect_legacy_financials import LegacyDartFinancialWorker
from earnings.collect_legacy_financials import _outcome_counter_key
from earnings.supabase_rest import EarningsStoreError


def archive(document: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zipped:
        zipped.writestr("report.xml", document.encode("utf-8"))
    return buffer.getvalue()


class LegacyDartFinancialParserTests(unittest.TestCase):
    def test_database_complete_outcome_uses_completed_summary_counter(self):
        self.assertEqual(_outcome_counter_key("complete"), "completed")
        self.assertEqual(_outcome_counter_key("review_required"), "review_required")

    def test_worker_indexes_historical_financials_by_company(self):
        worker = LegacyDartFinancialWorker(
            object(), object(), sleeper=lambda _seconds: None,
            historical_financials=[
                {"company_id": "a", "fiscal_year": 2020, "revenue": "100"},
                {"company_id": "b", "fiscal_year": 2020, "revenue": "200"},
            ],
        )
        self.assertEqual(len(worker._history_by_company["a"]), 1)
        self.assertEqual(worker._history_by_company["b"][0]["revenue"], "200")

    def test_failure_diagnostic_exposes_only_sanitized_store_errors(self):
        worker = object.__new__(LegacyDartFinancialWorker)
        self.assertEqual(
            worker._failure_diagnostic(EarningsStoreError("Earnings RPC failed: safe")),
            "Earnings RPC failed: safe",
        )
        try:
            raise RuntimeError("secret provider payload")
        except RuntimeError as error:
            diagnostic = worker._failure_diagnostic(error)
        self.assertRegex(diagnostic, r"^RuntimeError:test_failure_diagnostic_exposes_only_sanitized_store_errors:\d+$")
        self.assertNotIn("secret provider payload", diagnostic)

    def test_reads_current_cumulative_column_and_ignores_note_column(self):
        document = """
        <P>연결손익계산서</P><P>(단위 : 백만원)</P>
        <TABLE>
          <TR><TH>과목</TH><TH>주석</TH><TH>당분기</TH><TH>누적</TH><TH>전분기</TH><TH>누적</TH></TR>
          <TR><TD>매출액</TD><TD>4</TD><TD>30</TD><TD>100</TD><TD>20</TD><TD>80</TD></TR>
          <TR><TD>영업이익</TD><TD>5</TD><TD>6</TD><TD>18</TD><TD>4</TD><TD>12</TD></TR>
          <TR><TD>당기순이익</TD><TD>6</TD><TD>5</TD><TD>15</TD><TD>3</TD><TD>10</TD></TR>
        </TABLE>
        """
        parsed = parse_legacy_filing_archive(archive(document), report_code="11014")
        self.assertEqual(parsed["CFS"].revenue, 100_000_000)
        self.assertEqual(parsed["CFS"].operating_income, 18_000_000)
        self.assertEqual(parsed["CFS"].net_income, 15_000_000)

    def test_colspan_shift_cannot_turn_three_month_amount_into_ytd(self):
        document = """
        <P>연결 손익계산서</P><P>(단위 : 백만원)</P>
        <TABLE>
          <TR><TH COLSPAN="2">과목</TH><TH COLSPAN="2">제 47 기 반기</TH><TH COLSPAN="2">제 46 기 반기</TH></TR>
          <!-- Reproduce DART's shifted header: the first amount column inherits 누적. -->
          <TR><TH></TH><TH>3개월</TH><TH>누적</TH><TH>3개월</TH><TH>누적</TH></TR>
          <TR><TD COLSPAN="2">수익(매출액)</TD><TD>48,537,539</TD><TD>95,655,457</TD><TD>52,353,229</TD><TD>106,028,555</TD></TR>
          <TR><TD COLSPAN="2">매출원가</TD><TD>28,955,599</TD><TD>57,910,986</TD><TD>31,671,819</TD><TD>63,721,334</TD></TR>
          <TR><TD COLSPAN="2">매출총이익</TD><TD>19,581,940</TD><TD>37,744,471</TD><TD>20,681,410</TD><TD>42,307,221</TD></TR>
          <TR><TD COLSPAN="2">판매비와관리비</TD><TD>12,684,003</TD><TD>24,867,167</TD><TD>13,494,087</TD><TD>26,631,099</TD></TR>
          <TR><TD COLSPAN="2">영업이익(손실)</TD><TD>6,897,937</TD><TD>12,877,304</TD><TD>7,187,323</TD><TD>15,676,122</TD></TR>
          <TR><TD COLSPAN="2">기타수익</TD><TD>1</TD><TD>2</TD><TD>3</TD><TD>4</TD></TR>
          <TR><TD COLSPAN="2">기타비용</TD><TD>1</TD><TD>2</TD><TD>3</TD><TD>4</TD></TR>
          <TR><TD COLSPAN="2">당기순이익(손실)</TD><TD>5,752,297</TD><TD>10,378,112</TD><TD>6,250,781</TD><TD>13,825,222</TD></TR>
        </TABLE>
        """
        parsed = parse_legacy_filing_archive(archive(document), report_code="11012")
        self.assertEqual(parsed["CFS"].revenue, 95_655_457_000_000)
        self.assertEqual(parsed["CFS"].operating_income, 12_877_304_000_000)
        self.assertEqual(parsed["CFS"].net_income, 10_378_112_000_000)

    def test_keeps_separate_and_consolidated_candidates_separate(self):
        document = """
        <P>손익계산서</P><P>(단위 : 천원)</P>
        <TABLE>
          <TR><TH>과목</TH><TH>당기</TH><TH>전기</TH></TR>
          <TR><TD>매출액</TD><TD>100</TD><TD>90</TD></TR>
          <TR><TD>영업손익</TD><TD>(10)</TD><TD>8</TD></TR>
          <TR><TD>당기순이익(손실)</TD><TD>(12)</TD><TD>5</TD></TR>
        </TABLE>
        <P>연결손익계산서</P><P>(단위 : 천원)</P>
        <TABLE>
          <TR><TH>과목</TH><TH>당기</TH><TH>전기</TH></TR>
          <TR><TD>영업수익</TD><TD>200</TD><TD>180</TD></TR>
          <TR><TD>영업이익</TD><TD>20</TD><TD>15</TD></TR>
          <TR><TD>당기순이익</TD><TD>16</TD><TD>12</TD></TR>
        </TABLE>
        """
        parsed = parse_legacy_filing_archive(archive(document), report_code="11011")
        self.assertEqual(parsed["OFS"].operating_income, -10_000)
        self.assertEqual(parsed["CFS"].operating_income, 20_000)

    def test_ignores_layout_table_wrapping_multiple_statements(self):
        document = """
        <TABLE><TR><TD>
          <P>연결 포괄손익계산서</P><P>(단위 : 천원)</P>
          <TABLE>
            <TR><TH>과목</TH><TH>3개월</TH><TH>누적</TH><TH>전기 3개월</TH><TH>전기 누적</TH></TR>
            <TR><TD>매출액</TD><TD>16,000</TD><TD>35,000</TD><TD>18,000</TD><TD>38,000</TD></TR>
            <TR><TD>영업이익</TD><TD>400</TD><TD>1,100</TD><TD>(100)</TD><TD>600</TD></TR>
            <TR><TD>당기순이익</TD><TD>300</TD><TD>900</TD><TD>(200)</TD><TD>500</TD></TR>
          </TABLE>
          <P>포괄손익계산서</P><P>(단위 : 천원)</P>
          <TABLE>
            <TR><TH>과목</TH><TH>3개월</TH><TH>누적</TH><TH>전기 3개월</TH><TH>전기 누적</TH></TR>
            <TR><TD>매출액</TD><TD>600</TD><TD>900</TD><TD>500</TD><TD>800</TD></TR>
            <TR><TD>영업이익</TD><TD>60</TD><TD>90</TD><TD>50</TD><TD>80</TD></TR>
            <TR><TD>당기순이익</TD><TD>40</TD><TD>70</TD><TD>30</TD><TD>60</TD></TR>
          </TABLE>
        </TD></TR></TABLE>
        """
        parsed = parse_legacy_filing_archive(archive(document), report_code="11012")
        self.assertEqual(parsed["CFS"].revenue, 35_000_000)
        self.assertEqual(parsed["OFS"].revenue, 900_000)

    def test_scope_uses_last_statement_title_beyond_local_markup_window(self):
        spacer = "<SPAN data-padding='x'>padding</SPAN>" * 220
        document = f"""
        <P>연결 포괄손익계산서</P>{spacer}<P>(단위 : 천원)</P>
        <TABLE>
          <TR><TH>과목</TH><TH>당기</TH><TH>전기</TH></TR>
          <TR><TD>매출액</TD><TD>16,000</TD><TD>15,000</TD></TR>
          <TR><TD>영업이익</TD><TD>400</TD><TD>300</TD></TR>
          <TR><TD>당기순이익</TD><TD>300</TD><TD>200</TD></TR>
        </TABLE>
        <P>포괄손익계산서</P>{spacer}<P>(단위 : 천원)</P>
        <TABLE>
          <TR><TH>과목</TH><TH>당기</TH><TH>전기</TH></TR>
          <TR><TD>매출액</TD><TD>600</TD><TD>500</TD></TR>
          <TR><TD>영업이익</TD><TD>60</TD><TD>50</TD></TR>
          <TR><TD>당기순이익</TD><TD>40</TD><TD>30</TD></TR>
        </TABLE>
        """
        parsed = parse_legacy_filing_archive(archive(document), report_code="11013")
        self.assertEqual(parsed["CFS"].revenue, 16_000_000)
        self.assertEqual(parsed["OFS"].revenue, 600_000)

    def test_statement_title_inside_table_overrides_previous_scope(self):
        document = """
        <TABLE>
          <TR><TD>연결 포괄손익계산서</TD></TR>
          <TR><TD>(단위 : 천원)</TD></TR>
          <TR><TH>과목</TH><TH>당기</TH><TH>전기</TH></TR>
          <TR><TD>매출액</TD><TD>16,000</TD><TD>15,000</TD></TR>
          <TR><TD>영업이익</TD><TD>400</TD><TD>300</TD></TR>
          <TR><TD>당기순이익</TD><TD>300</TD><TD>200</TD></TR>
        </TABLE>
        <TABLE>
          <TR><TD>포괄손익계산서</TD></TR>
          <TR><TD>(단위 : 천원)</TD></TR>
          <TR><TH>과목</TH><TH>당기</TH><TH>전기</TH></TR>
          <TR><TD>매출액</TD><TD>600</TD><TD>500</TD></TR>
          <TR><TD>영업이익</TD><TD>60</TD><TD>50</TD></TR>
          <TR><TD>당기순이익</TD><TD>40</TD><TD>30</TD></TR>
        </TABLE>
        """
        parsed = parse_legacy_filing_archive(archive(document), report_code="11013")
        self.assertEqual(parsed["CFS"].revenue, 16_000_000)
        self.assertEqual(parsed["OFS"].revenue, 600_000)

    def test_detailed_statements_override_rounded_summary_tables(self):
        document = """
        <P>가. 요약연결재무정보</P><P>(단위 : 백만원)</P>
        <TABLE>
          <TR><TD>매출액</TD><TD>16,878,035</TD></TR>
          <TR><TD>영업이익</TD><TD>225,688</TD></TR>
          <TR><TD>당기순이익</TD><TD>96,288</TD></TR>
        </TABLE>
        <P>연결포괄손익계산서상 금액은 아래와 같습니다.</P>
        <P>나. 요약재무정보</P><P>(단위 : 백만원)</P>
        <TABLE>
          <TR><TD>매출액</TD><TD>591,851</TD></TR>
          <TR><TD>영업이익</TD><TD>292,552</TD></TR>
          <TR><TD>당기순이익</TD><TD>262,808</TD></TR>
        </TABLE>
        <P>연결 포괄손익계산서</P><P>(단위 : 천원)</P>
        <TABLE>
          <TR><TD>매출액</TD><TD>16,878,034,679</TD></TR>
          <TR><TD>영업이익</TD><TD>225,688,492</TD></TR>
          <TR><TD>순이익</TD><TD>96,288,363</TD></TR>
        </TABLE>
        <P>포괄손익계산서</P><P>(단위 : 천원)</P>
        <TABLE>
          <TR><TD>매출액</TD><TD>591,851,230</TD></TR>
          <TR><TD>영업이익</TD><TD>292,551,670</TD></TR>
          <TR><TD>순이익</TD><TD>262,807,675</TD></TR>
        </TABLE>
        """
        parsed = parse_legacy_filing_archive(archive(document), report_code="11013")
        self.assertEqual(parsed["CFS"].revenue, 16_878_034_679_000)
        self.assertEqual(parsed["CFS"].net_income, 96_288_363_000)
        self.assertEqual(parsed["OFS"].revenue, 591_851_230_000)

    def test_refuses_unknown_units(self):
        document = """
        <P>손익계산서</P>
        <TABLE>
          <TR><TD>매출액</TD><TD>100</TD></TR>
          <TR><TD>영업이익</TD><TD>10</TD></TR>
          <TR><TD>당기순이익</TD><TD>8</TD></TR>
        </TABLE>
        """
        with self.assertRaises(LegacyDartParseError):
            parse_legacy_filing_archive(archive(document), report_code="11011")

    def test_refuses_zero_revenue_statement_candidates(self):
        document = """
        <P>손익계산서</P><P>(단위 : 원)</P>
        <TABLE>
          <TR><TH>과목</TH><TH>당기</TH></TR>
          <TR><TD>매출액</TD><TD>0</TD></TR>
          <TR><TD>영업이익</TD><TD>0</TD></TR>
          <TR><TD>당기순이익</TD><TD>0</TD></TR>
        </TABLE>
        """
        with self.assertRaises(LegacyDartParseError):
            parse_legacy_filing_archive(archive(document), report_code="11011")

    def test_converts_cumulative_year_to_standalone_quarters(self):
        def statement(scope, revenue, operating, net):
            return LegacyCumulativeStatement(scope, revenue, operating, net)

        statements = {
            "11013": {"OFS": statement("OFS", 100, 20, 15)},
            "11012": {"OFS": statement("OFS", 230, 50, 35)},
            "11014": {"OFS": statement("OFS", 390, 90, 60)},
            "11011": {"OFS": statement("OFS", 600, 140, 95)},
        }
        scope, quarters = build_legacy_standalone_quarters(statements)
        self.assertEqual(scope, "OFS")
        self.assertEqual(quarters["11013"]["revenue"], 100)
        self.assertEqual(quarters["11012"]["revenue"], 130)
        self.assertEqual(quarters["11014"]["operating_income"], 40)
        self.assertEqual(quarters["11011"]["net_income"], 35)

    def test_refuses_to_mix_cfs_and_ofs_within_one_year(self):
        statements = {
            "11013": {"CFS": LegacyCumulativeStatement("CFS", 100, 20, 15)},
            "11012": {"OFS": LegacyCumulativeStatement("OFS", 200, 40, 30)},
        }
        scope, quarters = build_legacy_standalone_quarters(statements)
        self.assertIsNone(scope)
        self.assertEqual(quarters, {})

    def test_refuses_non_positive_standalone_revenue_after_subtraction(self):
        statements = {
            "11013": {"OFS": LegacyCumulativeStatement("OFS", 100, 20, 15)},
            "11012": {"OFS": LegacyCumulativeStatement("OFS", 100, 20, 15)},
            "11014": {"OFS": LegacyCumulativeStatement("OFS", 300, 60, 45)},
            "11011": {"OFS": LegacyCumulativeStatement("OFS", 500, 100, 75)},
        }
        _scope, quarters = build_legacy_standalone_quarters(statements)
        self.assertNotIn("11012", quarters)
        self.assertNotIn("11014", quarters, "잘못된 Q2 누적값 뒤의 Q3도 추정하지 않는다")
        self.assertEqual(quarters["11011"]["revenue"], 200)


if __name__ == "__main__":
    unittest.main()
