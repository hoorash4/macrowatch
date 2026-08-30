from datetime import date
import unittest

from earnings.market_breadth import MarketQuarter
from earnings.market_universe import quarterly_universes_from_rows


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


if __name__ == "__main__":
    unittest.main()
