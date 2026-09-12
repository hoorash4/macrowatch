import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.epiq_ch11 import extract_epiq_ch11


class EpiqChapter11Tests(unittest.TestCase):
    def test_current_and_prior_year_are_not_cross_assigned(self):
        text = (
            "NEW YORK/ALEXANDRIA – April 3, 2025 — "
            "Commercial chapter 11 bankruptcy filings increased 20 percent in March 2025, "
            "with filings climbing to 733 from the 611 filings registered in March 2024."
        )
        self.assertEqual(extract_epiq_ch11(text), [(2024, 3, 611), (2025, 3, 733)])

    def test_leading_count_and_prior_comparator(self):
        text = (
            "NEW YORK/ALEXANDRIA, May 6, 2026 — "
            "The 644 commercial Chapter 11 bankruptcy filings in April 2026 represented a 42% increase "
            "over the 454 filings recorded in April 2025."
        )
        self.assertEqual(extract_epiq_ch11(text), [(2025, 4, 454), (2026, 4, 644)])

    def test_publication_year_fills_month_when_article_omits_year(self):
        text = (
            "NEW YORK/ALEXANDRIA, VA – June 3, 2025 – "
            "Commercial chapter 11 filings totaled 733 in May, an increase of 62 percent over "
            "the 453 filings in April."
        )
        self.assertEqual(extract_epiq_ch11(text), [(2025, 4, 453), (2025, 5, 733)])

    def test_january_publication_maps_december_to_previous_year(self):
        text = (
            "NEW YORK/ALEXANDRIA – Jan. 3, 2025 — "
            "The 553 commercial chapter 11 filings in December represented an increase over last year."
        )
        self.assertEqual(extract_epiq_ch11(text), [(2024, 12, 553)])


if __name__ == "__main__":
    unittest.main()
