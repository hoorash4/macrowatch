from io import BytesIO
import pathlib
import sys
import unittest

from openpyxl import Workbook

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.court_rehabilitation import (
    LARGE_CATEGORY,
    MIDDLE_CATEGORY,
    SMALL_CATEGORY,
    _parse_monthly_filings,
)


class CourtRehabilitationTests(unittest.TestCase):
    def workbook(self, rows):
        wb = Workbook()
        ws = wb.active
        ws.title = "회생합의"
        for row in rows:
            ws.append(row)
        stream = BytesIO()
        wb.save(stream)
        wb.close()
        return stream.getvalue()

    def test_official_category_is_corporate_rehabilitation_agreement_case(self):
        self.assertEqual((LARGE_CATEGORY, MIDDLE_CATEGORY, SMALL_CATEGORY), ("G01", "T09", "S05"))

    def test_parser_reads_monthly_receipts_not_year_to_date_total(self):
        content = self.workbook([
            ["법원", "접수", "누계", "처리"],
            ["서울회생법원", 34, 257, 32],
            ["총계", 112, 843, 131],
            ["금년누계", 843, None, 850],
        ])
        self.assertEqual(_parse_monthly_filings(content), 112)

    def test_parser_rejects_workbook_without_total_receipts(self):
        content = self.workbook([["법원", "접수"], ["서울회생법원", 10]])
        with self.assertRaises(RuntimeError):
            _parse_monthly_filings(content)


if __name__ == "__main__":
    unittest.main()
