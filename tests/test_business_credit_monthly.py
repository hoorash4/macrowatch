import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.business_credit_monthly import _extract_epiq_ch11, _extract_equifax_levels


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

    def test_epiq_explicit_month_counts(self):
        text = (
            "There were 539 commercial Chapter 11 filings recorded in January 2025. "
            "The 644 commercial Chapter 11 bankruptcy filings in April 2026 increased 42 percent."
        )
        self.assertEqual(_extract_epiq_ch11(text), [(2025, 1, 539), (2026, 4, 644)])


if __name__ == "__main__":
    unittest.main()
