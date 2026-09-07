from __future__ import annotations

import unittest
from io import BytesIO
from zipfile import ZipFile

from earnings_v25.diagnose_raw import inspect_raw_archive


def archive(document: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zipped:
        zipped.writestr("report.xml", document.encode("utf-8"))
    return buffer.getvalue()


class EarningsV25RawDiagnosticTests(unittest.TestCase):
    def test_preserves_unrecognized_financial_top_line_label(self) -> None:
        document = """
        <P>연결손익계산서</P><P>2016년 1월 1일부터 2016년 3월 31일까지</P>
        <P>(단위 : 백만원)</P><TABLE>
          <TR><TH>과목</TH><TH>당분기</TH><TH>전분기</TH></TR>
          <TR><TD>보험영업수익</TD><TD>100</TD><TD>90</TD></TR>
          <TR><TD>영업이익</TD><TD>10</TD><TD>9</TD></TR>
          <TR><TD>분기순이익</TD><TD>8</TD><TD>7</TD></TR>
        </TABLE>
        """

        result = inspect_raw_archive(
            archive(document), report_code="11013", fiscal_year=2016,
        )

        rows = result["tables"][0]["rows"]
        self.assertEqual(rows[0]["label"], "보험영업수익")
        self.assertIsNone(rows[0]["recognized_metric"])
        self.assertEqual(rows[1]["recognized_metric"], "operating_income")
        self.assertEqual(rows[2]["recognized_metric"], "net_income")


if __name__ == "__main__":
    unittest.main()

