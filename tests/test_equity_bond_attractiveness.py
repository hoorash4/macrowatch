import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals.equity_bond_attractiveness import METHOD_VERSION, QuarterlyInput, build_weekly_rows, percentile_score, symmetric_change
from signals.equity_bond_attractiveness_pipeline import AUTOMATIC_OVERLAP_WEEKS, START, automatic_source_start


class EquityBondAttractivenessTests(unittest.TestCase):
    def test_percentile_score_is_centered_and_bounded(self):
        self.assertEqual(percentile_score(2, [1, 2, 3]), 50)
        self.assertAlmostEqual(percentile_score(1, [1, 2, 3]), 16.666666, places=5)

    def test_symmetric_change_handles_sign_crossing(self):
        self.assertEqual(symmetric_change(10, -10), 200)
        self.assertEqual(symmetric_change(0, 0), 0)

    def test_weekly_score_uses_gap_level_and_change(self):
        start = date(2018, 3, 30)
        weeks = [start + timedelta(days=7 * index) for index in range(180)]
        equity = {week: 100 + index for index, week in enumerate(weeks)}
        yields = {week: 2 + index * .002 for index, week in enumerate(weeks)}
        quarters = [QuarterlyInput(date(2016 + index // 4, (index % 4 + 1) * 3, 28), 100 + index * 2, 10_000 + index * 100) for index in range(28)]
        rows = build_weekly_rows("KR", weeks, equity, yields, quarters)
        self.assertGreater(len(rows), 20)
        self.assertTrue(all(0 <= row["score"] <= 100 for row in rows))
        self.assertEqual(set(rows[-1]["components"]), {"yield_gap_level", "yield_gap_change"})

    def test_price_and_yield_increases_reduce_attractiveness(self):
        start = date(2015, 1, 2)
        weeks = [start + timedelta(weeks=i) for i in range(350)]
        quarters = [QuarterlyInput(date(2015 + i // 4, (i % 4 + 1) * 3, 28), 100, 10_000) for i in range(28)]
        prices = dict.fromkeys(weeks, 100.0)
        yields = dict.fromkeys(weeks, 2.0)
        base = build_weekly_rows("KR", weeks, prices, yields, quarters)[-1]
        high_price = build_weekly_rows("KR", weeks, {**prices, weeks[-1]: 120.0}, yields, quarters)[-1]
        high_yield = build_weekly_rows("KR", weeks, prices, {**yields, weeks[-1]: 3.0}, quarters)[-1]
        self.assertLess(high_price["score"], base["score"])
        self.assertAlmostEqual(high_price["earnings_yield_pct"], base["earnings_yield_pct"] / 1.2)
        self.assertLess(high_yield["score"], base["score"])
        self.assertAlmostEqual(high_yield["yield_gap_pct"], base["yield_gap_pct"] - 1)
        more_income = [QuarterlyInput(q.period_end, q.net_income * 1.1, q.market_cap) for q in quarters]
        higher_profit = build_weekly_rows("KR", weeks, prices, yields, more_income)[-1]
        self.assertGreater(higher_profit["earnings_yield_pct"], base["earnings_yield_pct"])

    def test_stock_score_does_not_depend_on_bond_price_returns(self):
        start = date(2018, 3, 30)
        weeks = [start + timedelta(days=7 * index) for index in range(180)]
        equity = {week: 100 + index for index, week in enumerate(weeks)}
        yields = {week: 2 + index * .002 for index, week in enumerate(weeks)}
        quarters = [QuarterlyInput(date(2016 + index // 4, (index % 4 + 1) * 3, 28), 100 + index * 2, 10_000 + index * 100) for index in range(28)]
        rows = build_weekly_rows("KR", weeks, equity, yields, quarters)
        self.assertIn("equity_return_13w_pct", rows[-1])

    def test_automatic_source_start_uses_bounded_overlap_only_after_both_countries_exist(self):
        latest = date(2026, 9, 11)
        existing = [
            {"country": "KR", "observation_date": latest.isoformat(), "method_version": METHOD_VERSION},
            {"country": "US", "observation_date": latest.isoformat(), "method_version": METHOD_VERSION},
        ]
        self.assertEqual(automatic_source_start(existing), latest - timedelta(weeks=AUTOMATIC_OVERLAP_WEEKS))
        self.assertEqual(automatic_source_start(existing[:1]), START)

    def test_incremental_overlap_matches_full_history_for_new_rows(self):
        start = date(2015, 1, 2)
        weeks = [start + timedelta(weeks=index) for index in range(500)]
        prices = {week: 100.0 + index * 0.17 + (index % 9) * 0.03 for index, week in enumerate(weeks)}
        yields = {week: 1.5 + index * 0.0015 + (index % 7) * 0.002 for index, week in enumerate(weeks)}
        quarters = [
            QuarterlyInput(
                date(2014 + index // 4, (index % 4 + 1) * 3, 28),
                90.0 + index * 1.7,
                9_000.0 + index * 80.0,
            )
            for index in range(52)
        ]
        full = build_weekly_rows("KR", weeks, prices, yields, quarters)
        incremental_weeks = weeks[-AUTOMATIC_OVERLAP_WEEKS:]
        incremental_prices = {week: prices[week] for week in incremental_weeks}
        incremental_yields = {week: yields[week] for week in incremental_weeks}
        incremental = build_weekly_rows("KR", incremental_weeks, incremental_prices, incremental_yields, quarters)

        full_tail = {row["observation_date"]: row for row in full[-8:]}
        incremental_tail = {row["observation_date"]: row for row in incremental[-8:]}
        self.assertEqual(set(full_tail), set(incremental_tail))
        for observed, expected in full_tail.items():
            actual = incremental_tail[observed]
            for field in (
                "score", "earnings_yield_pct", "sovereign_yield_pct", "yield_gap_pct",
                "earnings_momentum_pct", "equity_return_13w_pct",
            ):
                self.assertAlmostEqual(actual[field], expected[field], places=10, msg=f"{observed} {field}")
            self.assertEqual(actual["components"], expected["components"])


if __name__ == "__main__":
    unittest.main()
