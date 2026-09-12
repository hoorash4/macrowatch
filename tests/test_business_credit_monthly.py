import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.business_credit_monthly import (
    _extract_epiq_ch11,
    _extract_equifax_levels,
    _extract_kdi_corporate_delinquency,
)
from sources.epiq_ch11_source import extract_epiq_ch11


class BusinessCreditParserTests(unittest.TestCase):
    def test_equifax_old_index_analysis(self):
        text = """
        The Equifax Small Business Delinquency Index (SBDI) 31–90 Days Past Due edged down 2bp in April to 1.19%.
        The SBDI 91–180 Days Past Due also fell 1bp to 0.34% and is down 21bp Y/Y.
        Defaults eased 5bp to 1.77%, 120bp below year-ago levels.
        """
        self.assertEqual(_extract_equifax_levels(text), (1.19, 0.34, 1.77))

    def test_equifax_new_level_block(self):
        text = """
        SBDI 31-90 Days ▼2bps (M/M) ▼8bps (Y/Y) 1.66% (Level)
        SBDI 91-180 Days ▲0bps (M/M) ▲2bps (Y/Y) 0.71% (Level)
        SBDFI ▼6bps (M/M) ▼19bps (Y/Y) 3.19% (Level)
        """
        self.assertEqual(_extract_equifax_levels(text), (1.66, 0.71, 3.19))

    def test_equifax_2024_block_without_level_labels(self):
        text = """
        SBLI 3MMA 96th Percentile ▼0.6% (M/M) ▲3.2% (Y/Y) 150.2
        SBDI 31-90 Days ▲0bps (M/M) ▲31bps (Y/Y) 1.71%
        SBDI 91-180 Days ▲2bps (M/M) ▲17bps (Y/Y) 0.57%
        SBDFI ▲5bps (M/M) ▲90bps (Y/Y) 2.91%
        SBLI 96th Percentile ▲5.6% (M/M) ▲7.5% (Y/Y) 154.0
        """
        self.assertEqual(_extract_equifax_levels(text), (1.71, 0.57, 2.91))

    def test_equifax_2026_level_allows_space_before_percent(self):
        text = """
        SBDI 31 -90 Days ▲3bps (M/M) ▼1bps (Y/Y) 1.72% (Level)
        SBDI 91 -180 Days ▲1bps (M/M) ▲3bps (Y/Y) 0.73% (Level)
        SBDFI ▼4bps (M/M) ▼5bps (Y/Y) 3.27 % (Level)
        SBLI 46th Percentile ▼9.0% (M/M) ▼13.1% (Y/Y) 124.9 (Level)
        """
        self.assertEqual(_extract_equifax_levels(text), (1.72, 0.73, 3.27))

    def test_equifax_does_not_read_sbli_percent_as_default_level(self):
        text = """
        SBDI 31-90 Days ▲0bps (M/M) ▲31bps (Y/Y) 1.71%
        SBDI 91-180 Days ▲2bps (M/M) ▲17bps (Y/Y) 0.57%
        SBDFI ▲5bps (M/M) ▲90bps (Y/Y) 2.91%
        SBLI ▼0.6% (M/M) ▲3.2% (Y/Y) 150.2
        """
        self.assertEqual(_extract_equifax_levels(text), (1.71, 0.57, 2.91))

    def test_epiq_explicit_month_counts(self):
        text = (
            "There were 539 commercial Chapter 11 filings recorded in January 2025. "
            "The 644 commercial Chapter 11 bankruptcy filings in April 2026 increased 42 percent."
        )
        self.assertEqual(_extract_epiq_ch11(text), [(2025, 1, 539), (2026, 4, 644)])

    def test_epiq_current_and_prior_year_are_not_cross_assigned(self):
        text = (
            "NEW YORK/ALEXANDRIA – April 3, 2025 — "
            "Commercial chapter 11 bankruptcy filings increased 20 percent in March 2025, "
            "with filings climbing to 733 from the 611 filings registered in March 2024."
        )
        self.assertEqual(extract_epiq_ch11(text), [(2024, 3, 611), (2025, 3, 733)])

    def test_epiq_leading_count_and_prior_comparator(self):
        text = (
            "NEW YORK/ALEXANDRIA, May 6, 2026 — "
            "The 644 commercial Chapter 11 bankruptcy filings in April 2026 represented a 42% increase "
            "over the 454 filings recorded in April 2025."
        )
        self.assertEqual(extract_epiq_ch11(text), [(2025, 4, 454), (2026, 4, 644)])

    def test_epiq_omitted_year_uses_publication_month(self):
        text = (
            "NEW YORK/ALEXANDRIA, VA – June 3, 2025 – "
            "Commercial chapter 11 filings totaled 733 in May, an increase of 62 percent over "
            "the 453 filings in April."
        )
        self.assertEqual(extract_epiq_ch11(text), [(2025, 4, 453), (2025, 5, 733)])

    def test_epiq_january_publication_maps_december_to_previous_year(self):
        text = (
            "NEW YORK/ALEXANDRIA – Jan. 3, 2025 — "
            "The 553 commercial chapter 11 filings in December represented an increase over last year."
        )
        self.assertEqual(extract_epiq_ch11(text), [(2024, 12, 553)])

    def test_kdi_fss_corporate_delinquency_two_digit_year(self):
        text = "‘17.12월말 국내은행의 원화대출 연체율 현황. (기업대출) ‘17.12월말 현재 기업대출(원화) 연체율은 0.47%로 전월말 대비 하락"
        self.assertEqual(_extract_kdi_corporate_delinquency(text), (2017, 12, 0.47))

    def test_kdi_fss_corporate_delinquency_four_digit_year(self):
        text = "2024년 12월말 국내은행의 원화대출 연체율 현황. (기업대출) ’24.12월말 현재 기업대출 연체율(0.50%)은 전월말 대비 하락"
        self.assertEqual(_extract_kdi_corporate_delinquency(text), (2024, 12, 0.50))


if __name__ == "__main__":
    unittest.main()
