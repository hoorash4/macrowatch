import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.business_credit_monthly import (
    _extract_epiq_ch11,
    _extract_equifax_changes,
    _extract_equifax_levels,
    _extract_kdi_corporate_delinquency,
)


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
        self.assertEqual(
            _extract_equifax_changes(text),
            {
                "US_SBDI_31_90": (1.66, -2.0, -8.0),
                "US_SBDI_91_180": (0.71, 0.0, 2.0),
                "US_SBDFI": (3.19, -6.0, -19.0),
            },
        )

    def test_equifax_changes_reproduce_published_previous_and_year_ago_values(self):
        text = """
        SBDI 31-90 Days ▲1bps (M/M) ▲30bps (Y/Y) 1.74% (Level)
        SBDI 91-180 Days ▲2bps (M/M) ▲21bps (Y/Y) 0.62% (Level)
        SBDFI ▲10bps (M/M) ▲98bps (Y/Y) 3.11% (Level)
        """
        short, severe, default = (
            _extract_equifax_changes(text)["US_SBDI_31_90"],
            _extract_equifax_changes(text)["US_SBDI_91_180"],
            _extract_equifax_changes(text)["US_SBDFI"],
        )
        self.assertAlmostEqual(short[0] - short[1] / 100, 1.73)
        self.assertAlmostEqual(short[0] - short[2] / 100, 1.44)
        self.assertAlmostEqual(severe[0] - severe[1] / 100, 0.60)
        self.assertAlmostEqual(default[0] - default[2] / 100, 2.13)

    def test_epiq_explicit_month_counts(self):
        text = (
            "There were 539 commercial Chapter 11 filings recorded in January 2025. "
            "The 644 commercial Chapter 11 bankruptcy filings in April 2026 increased 42 percent."
        )
        self.assertEqual(_extract_epiq_ch11(text), [(2025, 1, 539), (2026, 4, 644)])

    def test_kdi_fss_corporate_delinquency_two_digit_year(self):
        text = "‘17.12월말 국내은행의 원화대출 연체율 현황. (기업대출) ‘17.12월말 현재 기업대출(원화) 연체율은 0.47%로 전월말 대비 하락"
        self.assertEqual(_extract_kdi_corporate_delinquency(text), (2017, 12, 0.47))

    def test_kdi_fss_corporate_delinquency_four_digit_year(self):
        text = "2024년 12월말 국내은행의 원화대출 연체율 현황. (기업대출) ’24.12월말 현재 기업대출 연체율(0.50%)은 전월말 대비 하락"
        self.assertEqual(_extract_kdi_corporate_delinquency(text), (2024, 12, 0.50))


if __name__ == "__main__":
    unittest.main()
