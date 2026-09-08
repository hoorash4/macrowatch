import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from equity_bond_attractiveness import QuarterlyInput, build_weekly_rows, midrank_score, symmetric_change


class EquityBondAttractivenessTests(unittest.TestCase):
    def test_midrank_score_is_centered_and_bounded(self):
        self.assertEqual(midrank_score(2, [1, 2, 3]), 0)
        self.assertAlmostEqual(midrank_score(1, [1, 2, 3]), -66.666666, places=5)

    def test_symmetric_change_handles_sign_crossing(self):
        self.assertEqual(symmetric_change(10, -10), 200)
        self.assertEqual(symmetric_change(0, 0), 0)

    def test_weekly_score_uses_all_three_components(self):
        start = date(2018, 3, 30)
        weeks = [start + timedelta(days=7 * index) for index in range(180)]
        equity = {week: 100 + index for index, week in enumerate(weeks)}
        bond = {week: 100 + index * .1 for index, week in enumerate(weeks)}
        yields = {week: 2 + index * .002 for index, week in enumerate(weeks)}
        quarters = [QuarterlyInput(date(2016 + index // 4, (index % 4 + 1) * 3, 28), 100 + index * 2, 10_000 + index * 100) for index in range(28)]
        rows = build_weekly_rows("KR", weeks, equity, bond, yields, quarters)
        self.assertGreater(len(rows), 20)
        self.assertTrue(all(-100 <= row["score"] <= 100 for row in rows))
        self.assertEqual(set(rows[-1]["components"]), {"yield_gap", "earnings_momentum", "relative_return"})


if __name__ == "__main__":
    unittest.main()
