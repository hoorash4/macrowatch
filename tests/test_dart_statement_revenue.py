from decimal import Decimal
from io import BytesIO
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.dart_statement_revenue import (  # noqa: E402
    DartRevenueDerivationError,
    derive_gross_revenue,
    derive_gross_revenue_from_account_rows,
    derive_gross_revenue_from_archive,
    derive_gross_revenue_from_xbrl_presentation,
    find_income_statement_document,
)


def statement(rows, unit="백만원"):
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<html><body>(단위 : {unit})<table>{body}</table></body></html>"


class DartStatementRevenueTests(unittest.TestCase):
    def test_connected_income_statement_leaf_is_selected(self):
        filing = """
        node2['text'] = "2. 연결재무제표";
        node2['dcmNo'] = "10";
        node2['eleId'] = "17";
        node2['offset'] = "100";
        node2['length'] = "500";
        node3['text'] = "2-2. 연결 포괄손익계산서";
        node3['dcmNo'] = "10";
        node3['eleId'] = "19";
        node3['offset'] = "200";
        node3['length'] = "300";
        node3['dtd'] = "dart4.xsd";
        node3['text'] = "4-2. 포괄손익계산서";
        node3['dcmNo'] = "10";
        node3['eleId'] = "29";
        node3['offset'] = "600";
        node3['length'] = "300";
        """
        document = find_income_statement_document(filing, consolidation_scope="CFS")
        self.assertEqual(document.element_id, "19")
        self.assertEqual(document.title, "2-2. 연결 포괄손익계산서")

    def test_parent_child_duplicates_are_removed_and_both_periods_reconcile(self):
        page = statement([
            ("순이자이익", "250", "500"),
            ("　이자수익", "500", "1,000"),
            ("　이자비용", "(250)", "(500)"),
            ("순수수료수익(비용)", "150", "300"),
            ("　수수료수익", "200", "300"),
            ("　수수료비용", "(50)", "0"),
            ("일반관리비", "(300)", "(600)"),
            ("영업이익", "100", "200"),
        ])
        result = derive_gross_revenue(
            page,
            operating_current=Decimal("100000000"),
            operating_cumulative=Decimal("200000000"),
        )
        self.assertEqual(result.current_revenue, Decimal("700000000"))
        self.assertEqual(result.current_expense, Decimal("600000000"))
        self.assertEqual(result.cumulative_revenue, Decimal("1300000000"))
        self.assertEqual(result.cumulative_expense, Decimal("1100000000"))

    def test_original_xml_aindent_preserves_the_account_tree(self):
        page = """
          <DOCUMENT>(단위 : 원)<TABLE>
            <TR><TD><P AINDENT="0">순이자이익</P></TD><TD>50</TD></TR>
            <TR><TD><P AINDENT="1">이자수익</P></TD><TD>100</TD></TR>
            <TR><TD><P AINDENT="1">이자비용</P></TD><TD>50</TD></TR>
            <TR><TD><P AINDENT="0">영업이익</P></TD><TD>50</TD></TR>
          </TABLE></DOCUMENT>
        """
        result = derive_gross_revenue(
            page, operating_current=Decimal("50"), operating_cumulative=None,
        )
        self.assertEqual(result.current_revenue, Decimal("100"))
        self.assertEqual(result.current_expense, Decimal("50"))

    def test_insurance_revenue_descends_past_net_results(self):
        page = statement([
            ("보험손익", "200"),
            ("　보험영업수익", "600"),
            ("　보험영업비용", "400"),
            ("투자손익", "100"),
            ("　투자영업수익", "300"),
            ("　투자영업비용", "200"),
            ("영업이익", "300"),
        ], unit="원")
        result = derive_gross_revenue(
            page, operating_current=Decimal("300"), operating_cumulative=None,
        )
        self.assertEqual(result.current_revenue, Decimal("900"))
        self.assertEqual(result.current_expense, Decimal("600"))

    def test_mixed_provision_caption_uses_word_order_and_actual_reversal_is_revenue(self):
        page = statement([
            ("순수수료손익", "80"),
            ("　수수료수익", "100"),
            ("　수수료비용", "20"),
            ("신용손실충당금전입(환입)액", "10"),
            ("기타영업손익", "50"),
            ("　금융자산신용손실환입", "10"),
            ("　기타영업수익", "90"),
            ("　금융자산신용손실", "20"),
            ("　기타영업비용", "30"),
            ("판매비와관리비", "30"),
            ("영업이익", "90"),
        ], unit="원")
        result = derive_gross_revenue(
            page, operating_current=Decimal("90"), operating_cumulative=None,
        )
        self.assertEqual(result.current_revenue, Decimal("200"))
        self.assertEqual(result.current_expense, Decimal("110"))

    def test_non_reconciling_statement_is_rejected(self):
        page = statement([
            ("영업수익", "100"),
            ("영업비용", "20"),
            ("영업이익", "70"),
        ], unit="원")
        with self.assertRaises(DartRevenueDerivationError):
            derive_gross_revenue(
                page, operating_current=Decimal("70"), operating_cumulative=None,
            )

    def test_official_archive_selects_only_the_reconciling_statement_table(self):
        output = BytesIO()
        with ZipFile(output, "w") as zipped:
            zipped.writestr("report.xml", """
              <DOCUMENT>(단위 : 원)
                <TABLE><TR><TD>영업수익</TD><TD>999</TD></TR>
                  <TR><TD>영업이익</TD><TD>10</TD></TR></TABLE>
                (단위 : 원)
                <TABLE><TR><TD>영업수익</TD><TD>300</TD></TR>
                  <TR><TD>영업비용</TD><TD>200</TD></TR>
                  <TR><TD>영업이익</TD><TD>100</TD></TR></TABLE>
              </DOCUMENT>
            """)
        result = derive_gross_revenue_from_archive(
            output.getvalue(),
            operating_current=Decimal("100"),
            operating_cumulative=None,
        )
        self.assertEqual(result.current_revenue, Decimal("300"))

    def test_archive_accepts_cumulative_only_interim_statement(self):
        output = BytesIO()
        with ZipFile(output, "w") as zipped:
            zipped.writestr("report.xml", """
              <DOCUMENT>(단위 : 원)<TABLE>
                <TR><TD>영업수익</TD><TD>900</TD><TD>700</TD></TR>
                <TR><TD>영업비용</TD><TD>700</TD><TD>600</TD></TR>
                <TR><TD>영업이익</TD><TD>200</TD><TD>100</TD></TR>
              </TABLE></DOCUMENT>
            """)
        result = derive_gross_revenue_from_archive(
            output.getvalue(),
            operating_current=Decimal("125"),
            operating_cumulative=Decimal("200"),
        )
        self.assertIsNone(result.current_revenue)
        self.assertEqual(result.cumulative_revenue, Decimal("900"))

    def test_archive_scope_excludes_separate_statement_with_same_operating_income(self):
        output = BytesIO()
        with ZipFile(output, "w") as zipped:
            zipped.writestr("report.xml", """
              <DOCUMENT>
                연결 포괄손익계산서 (단위 : 원)
                <TABLE><TR><TD>영업수익</TD><TD>300</TD></TR>
                  <TR><TD>영업비용</TD><TD>200</TD></TR>
                  <TR><TD>영업이익</TD><TD>100</TD></TR></TABLE>
                포괄손익계산서 (단위 : 원)
                <TABLE><TR><TD>영업수익</TD><TD>250</TD></TR>
                  <TR><TD>영업비용</TD><TD>150</TD></TR>
                  <TR><TD>영업이익</TD><TD>100</TD></TR></TABLE>
              </DOCUMENT>
            """)
        result = derive_gross_revenue_from_archive(
            output.getvalue(), operating_current=Decimal("100"),
            operating_cumulative=None, consolidation_scope="CFS",
        )
        self.assertEqual(result.current_revenue, Decimal("300"))

    def test_archive_keeps_outer_statement_table_when_layout_table_is_nested(self):
        output = BytesIO()
        with ZipFile(output, "w") as zipped:
            zipped.writestr("report.xml", """
              <DOCUMENT>연결 손익계산서 (단위 : 원)
                <TABLE>
                  <TR><TD>영업수익</TD><TD>300</TD></TR>
                  <TABLE><TR><TD>레이아웃</TD><TD>-</TD></TR></TABLE>
                  <TR><TD>영업비용</TD><TD>200</TD></TR>
                  <TR><TD>영업이익</TD><TD>100</TD></TR>
                </TABLE>
              </DOCUMENT>
            """)
        result = derive_gross_revenue_from_archive(
            output.getvalue(), operating_current=Decimal("100"),
            operating_cumulative=None, consolidation_scope="CFS",
        )
        self.assertEqual(result.current_revenue, Decimal("300"))

    def test_archive_combines_sibling_account_tables_under_statement_title(self):
        output = BytesIO()
        with ZipFile(output, "w") as zipped:
            zipped.writestr("report.xml", """
              <DOCUMENT><TITLE>연결 손익계산서</TITLE>(단위 : 원)
                <TABLE><TR><TD>영업수익</TD><TD>300</TD></TR></TABLE>
                <TABLE><TR><TD>영업비용</TD><TD>200</TD></TR></TABLE>
                <TABLE><TR><TD>영업이익</TD><TD>100</TD></TR></TABLE>
              </DOCUMENT>
            """)
        result = derive_gross_revenue_from_archive(
            output.getvalue(), operating_current=Decimal("100"),
            operating_cumulative=None, consolidation_scope="CFS",
        )
        self.assertEqual(result.current_revenue, Decimal("300"))


    def test_archive_walks_back_past_repeated_statement_headings(self):
        output = BytesIO()
        with ZipFile(output, "w") as zipped:
            zipped.writestr("report.xml", """
              <DOCUMENT><TITLE>연결 손익계산서</TITLE>(단위 : 원)
                <TABLE><TR><TD>영업수익</TD><TD>300</TD></TR></TABLE>
                <TITLE>연결 손익계산서</TITLE>
                <TABLE><TR><TD>영업비용</TD><TD>200</TD></TR></TABLE>
                <TITLE>연결 손익계산서</TITLE>
                <TABLE><TR><TD>영업이익</TD><TD>100</TD></TR></TABLE>
              </DOCUMENT>
            """)
        result = derive_gross_revenue_from_archive(
            output.getvalue(), operating_current=Decimal("100"),
            operating_cumulative=None, consolidation_scope="CFS",
        )
        self.assertEqual(result.current_revenue, Decimal("300"))



    def test_archive_expands_to_account_tables_before_repeated_title(self):
        output = BytesIO()
        layout = "".join(
            f"<TABLE><TR><TD>레이아웃{index}</TD><TD>-</TD></TR></TABLE>"
            for index in range(30)
        )
        with ZipFile(output, "w") as zipped:
            zipped.writestr(
                "report.xml",
                "(단위 : 원)"
                "<TABLE><TR><TD>영업수익</TD><TD>300</TD></TR></TABLE>"
                + layout
                + "<TITLE>연결 손익계산서</TITLE>"
                "<TABLE><TR><TD>영업비용</TD><TD>200</TD></TR></TABLE>"
                "<TABLE><TR><TD>영업이익</TD><TD>100</TD></TR></TABLE>",
            )
        result = derive_gross_revenue_from_archive(
            output.getvalue(), operating_current=Decimal("100"),
            operating_cumulative=None, consolidation_scope="CFS",
        )
        self.assertEqual(result.current_revenue, Decimal("300"))



    def test_full_account_rows_reconcile_shinhan_financial_revenue(self):
        def row(name, current, cumulative, account_id="custom"):
            return {
                "sj_div": "CIS",
                "account_id": account_id,
                "account_nm": name,
                "thstrm_amount": str(current),
                "thstrm_add_amount": str(cumulative),
            }

        rows = [
            row("기타포괄손익-공정가치측정유가증권 처분손익", -7323, 13743),
            row("당기손익-공정가치측정금융상품 관련손익", 2136947, 2725906),
            row("보험금융손익", -1963797, -2387378),
            row("보험금융비용", 1965577, 2408119),
            row("보험금융수익", 1780, 20741),
            row("순수수료손익", 1188929, 2129755),
            row("수수료비용", 473598, 894651),
            row("수수료수익", 1662527, 3024406),
            row("상각후원가측정유가증권 처분손익", -5, -19),
            row("당기손익-공정가치측정지정금융상품 관련손익", -134215, -14292),
            row("일반관리비", 1670691, 3216096),
            row("신용손실충당금 전입액", 456313, 976425),
            row("순보험손익", 153042, 429439),
            row("재보험서비스비용", 43658, 90911),
            row("재보험수익", 56281, 108475),
            row("보험수익", 916355, 1797939),
            row("보험서비스비용", 775936, 1386064),
            row("순이자손익", 3134361, 6158504),
            row("이자비용", 4147606, 8195594),
            row("이자수익", 7281967, 14354098),
            row("외환거래손익", 556020, 645039),
            row("기타영업손익", -526849, -1014478),
            row("배당수익", 66175, 137036),
            row(
                "관계기업의 기타포괄손익에 대한 지분", 221, 1276,
                "ifrs-full_ShareOfOtherComprehensiveIncomeOfAssociates",
            ),
            row(
                "관계기업의 기타포괄손익에 대한 지분", 5, 2,
                "ifrs-full_ShareOfOtherComprehensiveIncomeNotReclassified",
            ),
            row(
                "반기순이익", 1846651, 3495799,
                "ifrs-full_ProfitLoss",
            ),
            row(
                "법인세비용차감전순이익", 2515975, 4737357,
                "ifrs-full_ProfitLossBeforeTax",
            ),
            row("영업이익", 2476281, 4630734),
            row(
                "관계기업 이익에 대한 지분", 2344, 51183,
                "ifrs-full_ShareOfProfitLossOfAssociates",
            ),
        ]
        result = derive_gross_revenue_from_account_rows(
            rows,
            operating_current=Decimal("2476281"),
            operating_cumulative=Decimal("4630734"),
        )
        self.assertEqual(result.current_revenue, Decimal("12678052"))
        self.assertEqual(result.current_expense, Decimal("10201771"))
        self.assertEqual(result.cumulative_revenue, Decimal("22827383"))
        self.assertEqual(result.cumulative_expense, Decimal("18196649"))



    def test_xbrl_presentation_tree_removes_child_account_duplicates(self):
        rows = [
            {
                "sj_div": "CIS", "account_id": "test_NetInterest",
                "account_nm": "순이자손익", "thstrm_amount": "100",
                "thstrm_add_amount": "200",
            },
            {
                "sj_div": "CIS", "account_id": "test_InterestRevenue",
                "account_nm": "이자수익", "thstrm_amount": "300",
                "thstrm_add_amount": "600",
            },
            {
                "sj_div": "CIS", "account_id": "test_InterestExpense",
                "account_nm": "이자비용", "thstrm_amount": "200",
                "thstrm_add_amount": "400",
            },
            {
                "sj_div": "CIS", "account_id": "test_OperatingIncome",
                "account_nm": "영업이익", "thstrm_amount": "100",
                "thstrm_add_amount": "200",
            },
        ]
        presentation = """<?xml version="1.0" encoding="UTF-8"?>
          <link:linkbase
            xmlns:link="http://www.xbrl.org/2003/linkbase"
            xmlns:xlink="http://www.w3.org/1999/xlink">
            <link:presentationLink xlink:type="extended" xlink:role="test">
              <link:loc xlink:type="locator" xlink:label="root"
                xlink:href="schema.xsd#test_Statement"/>
              <link:loc xlink:type="locator" xlink:label="net"
                xlink:href="schema.xsd#test_NetInterest"/>
              <link:loc xlink:type="locator" xlink:label="revenue"
                xlink:href="schema.xsd#test_InterestRevenue"/>
              <link:loc xlink:type="locator" xlink:label="expense"
                xlink:href="schema.xsd#test_InterestExpense"/>
              <link:loc xlink:type="locator" xlink:label="op"
                xlink:href="schema.xsd#test_OperatingIncome"/>
              <link:presentationArc xlink:type="arc" xlink:from="root"
                xlink:to="net" order="1"/>
              <link:presentationArc xlink:type="arc" xlink:from="net"
                xlink:to="revenue" order="1"/>
              <link:presentationArc xlink:type="arc" xlink:from="net"
                xlink:to="expense" order="2"/>
              <link:presentationArc xlink:type="arc" xlink:from="root"
                xlink:to="op" order="2"/>
            </link:presentationLink>
          </link:linkbase>
        """
        output = BytesIO()
        with ZipFile(output, "w") as zipped:
            zipped.writestr("company_pre.xml", presentation)
        result = derive_gross_revenue_from_xbrl_presentation(
            output.getvalue(), rows,
            operating_current=Decimal("100"),
            operating_cumulative=Decimal("200"),
        )
        self.assertEqual(result.current_revenue, Decimal("300"))
        self.assertEqual(result.current_expense, Decimal("200"))
        self.assertEqual(result.cumulative_revenue, Decimal("600"))



    def test_financial_provision_without_amount_suffix_is_expense(self):
        page = statement([
            ("영업수익", "300"),
            ("신용손실충당금전입", "200"),
            ("영업이익", "100"),
        ], unit="원")
        result = derive_gross_revenue(
            page, operating_current=Decimal("100"), operating_cumulative=None,
        )
        self.assertEqual(result.current_revenue, Decimal("300"))
        self.assertEqual(result.current_expense, Decimal("200"))



    def test_xbrl_instance_supplies_company_extension_accounts(self):
        rows = [
            {
                "sj_div": "CIS", "account_id": "test_Revenue",
                "account_nm": "영업수익", "thstrm_amount": "300",
                "thstrm_add_amount": "600",
            },
            {
                "sj_div": "CIS", "account_id": "test_OperatingIncome",
                "account_nm": "영업이익", "thstrm_amount": "100",
                "thstrm_add_amount": "200",
            },
        ]
        presentation = """<link:linkbase
          xmlns:link="http://www.xbrl.org/2003/linkbase"
          xmlns:xlink="http://www.w3.org/1999/xlink">
          <link:presentationLink xlink:type="extended" xlink:role="test">
            <link:loc xlink:type="locator" xlink:label="root"
              xlink:href="schema.xsd#test_Statement"/>
            <link:loc xlink:type="locator" xlink:label="revenue"
              xlink:href="schema.xsd#test_Revenue"/>
            <link:loc xlink:type="locator" xlink:label="expense"
              xlink:href="schema.xsd#test_ExtensionExpense"/>
            <link:loc xlink:type="locator" xlink:label="op"
              xlink:href="schema.xsd#test_OperatingIncome"/>
            <link:presentationArc xlink:type="arc" xlink:from="root"
              xlink:to="revenue" order="1"/>
            <link:presentationArc xlink:type="arc" xlink:from="root"
              xlink:to="expense" order="2"/>
            <link:presentationArc xlink:type="arc" xlink:from="root"
              xlink:to="op" order="3"/>
          </link:presentationLink>
        </link:linkbase>"""
        labels = """<link:linkbase
          xmlns:link="http://www.xbrl.org/2003/linkbase"
          xmlns:xlink="http://www.w3.org/1999/xlink">
          <link:labelLink xlink:type="extended">
            <link:loc xlink:type="locator" xlink:label="expense-loc"
              xlink:href="schema.xsd#test_ExtensionExpense"/>
            <link:label xlink:type="resource" xlink:label="expense-label"
              xlink:role="http://www.xbrl.org/2003/role/label"
              xml:lang="ko">기타영업비용</link:label>
            <link:labelArc xlink:type="arc" xlink:from="expense-loc"
              xlink:to="expense-label"/>
          </link:labelLink>
        </link:linkbase>"""
        instance = """<xbrli:xbrl
          xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:test="http://example.com/test">
          <xbrli:context id="current"><xbrli:entity>
            <xbrli:identifier scheme="test">1</xbrli:identifier>
          </xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant>
          </xbrli:period></xbrli:context>
          <xbrli:context id="cumulative"><xbrli:entity>
            <xbrli:identifier scheme="test">1</xbrli:identifier>
          </xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant>
          </xbrli:period></xbrli:context>
          <test:Revenue contextRef="current">300</test:Revenue>
          <test:ExtensionExpense contextRef="current">200</test:ExtensionExpense>
          <test:OperatingIncome contextRef="current">100</test:OperatingIncome>
          <test:Revenue contextRef="cumulative">600</test:Revenue>
          <test:ExtensionExpense contextRef="cumulative">400</test:ExtensionExpense>
          <test:OperatingIncome contextRef="cumulative">200</test:OperatingIncome>
        </xbrli:xbrl>"""
        output = BytesIO()
        with ZipFile(output, "w") as zipped:
            zipped.writestr("company_pre.xml", presentation)
            zipped.writestr("company_lab-ko.xml", labels)
            zipped.writestr("company.xbrl", instance)
        result = derive_gross_revenue_from_xbrl_presentation(
            output.getvalue(), rows,
            operating_current=Decimal("100"),
            operating_cumulative=Decimal("200"),
        )
        self.assertEqual(result.current_revenue, Decimal("300"))
        self.assertEqual(result.current_expense, Decimal("200"))
        self.assertEqual(result.cumulative_revenue, Decimal("600"))



    def test_net_parent_allocates_undisclosed_child_remainder(self):
        page = statement([
            ("영업수익", "100"),
            ("순기타손익", "-50"),
            ("　기타영업비용", "20"),
            ("영업이익", "50"),
        ], unit="원")
        result = derive_gross_revenue(
            page, operating_current=Decimal("50"), operating_cumulative=None,
        )
        self.assertEqual(result.current_revenue, Decimal("100"))
        self.assertEqual(result.current_expense, Decimal("50"))



if __name__ == "__main__":
    unittest.main()
