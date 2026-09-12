import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.court_rehabilitation import (
    LARGE_CATEGORY,
    MIDDLE_CATEGORY,
    SMALL_CATEGORY,
    _parse_monthly_filings,
)


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows

    def iter_rows(self, values_only=False):
        return iter(self.rows)


class FakeWorkbook:
    def __init__(self, rows):
        self.sheet = FakeSheet(rows)
        self.sheetnames = ["회생합의"]
        self.worksheets = [self.sheet]

    def __getitem__(self, key):
        if key != "회생합의":
            raise KeyError(key)
        return self.sheet

    def close(self):
        pass


class CourtRehabilitationTests(unittest.TestCase):
    def parse(self, rows):
        with patch("sources.court_rehabilitation._load_workbook", return_value=FakeWorkbook(rows)):
            return _parse_monthly_filings(b"fixture")

    def test_official_category_is_corporate_rehabilitation_agreement_case(self):
        self.assertEqual((LARGE_CATEGORY, MIDDLE_CATEGORY, SMALL_CATEGORY), ("G01", "T09", "S05"))

    def test_parser_reads_monthly_receipts_not_year_to_date_total(self):
        value = self.parse([
            ("법원", "접수", "누계", "처리"),
            ("서울회생법원", 34, 257, 32),
            ("총계", 112, 843, 131),
            ("금년누계", 843, None, 850),
        ])
        self.assertEqual(value, 112)

    def test_parser_rejects_workbook_without_total_receipts(self):
        with self.assertRaises(RuntimeError):
            self.parse([("법원", "접수"), ("서울회생법원", 10)])


if __name__ == "__main__":
    unittest.main()
