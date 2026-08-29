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
    derive_gross_revenue_from_archive,
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


if __name__ == "__main__":
    unittest.main()
