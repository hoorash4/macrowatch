from datetime import date
import unittest

from earnings.market_breadth import MarketQuarter
from earnings.market_universe import (
    backfill_before_earliest_snapshot,
    quarterly_universes_from_rows,
)


class QuarterlyUniverseTests(unittest.TestCase):
    def test_uses_only_rpc_supplied_final_snapshot_and_preserves_period(self):
        rows = [
            {"index_id": "KOSPI100", "observed_on": "2026-03-31", "company_id": "a", "rank": 1},
            {"index_id": "KOSPI100", "observed_on": "2026-03-31", "company_id": "b", "rank": 2},
        ]
        result = quarterly_universes_from_rows(rows)["KOSPI100"][MarketQuarter(2026, 1)]
        self.assertEqual(result.observed_on, date(2026, 3, 31))
        self.assertEqual(result.company_ids, frozenset({"a", "b"}))

    def test_incomplete_ranks_fail_closed(self):
        with self.assertRaises(ValueError):
            quarterly_universes_from_rows([{
                "index_id": "KOSPI100", "observed_on": "2026-03-31",
                "company_id": "a", "rank": 2,
            }])

    def test_backfills_only_before_oldest_real_snapshot(self):
        q2 = MarketQuarter(2026, 2)
        q3 = MarketQuarter(2026, 3)
        q4 = MarketQuarter(2026, 4)
        original = {
            q3: quarterly_universes_from_rows([
                {"index_id": "KOSPI100", "observed_on": "2026-08-29", "company_id": "a", "rank": 1},
                {"index_id": "KOSPI100", "observed_on": "2026-08-29", "company_id": "b", "rank": 2},
            ])["KOSPI100"][q3]
        }
        result = backfill_before_earliest_snapshot(original, [q2, q3, q4])
        self.assertEqual(result[q2].company_ids, frozenset({"a", "b"}))
        self.assertEqual(result[q2].observed_on, date(2026, 8, 29))
        self.assertIs(result[q3], original[q3])
        self.assertNotIn(q4, result)


if __name__ == "__main__":
    unittest.main()
