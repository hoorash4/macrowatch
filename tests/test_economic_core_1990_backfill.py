import unittest
from datetime import date
from unittest.mock import patch

from backend.signals.economic_core_1990_backfill import _policy_rows, _spread_rows


class FakeDatabase:
    def request(self, method, table, **kwargs):
        if method == "GET" and table == "central_bank_policy_events":
            return [{"meeting_date": "2000-02-02"}, {"meeting_date": "2000-03-21"}]
        return []


class EconomicCoreBackfillTests(unittest.TestCase):
    @patch("backend.signals.economic_core_1990_backfill.fetch_us_policy_rate_chart_rows")
    @patch("backend.signals.economic_core_1990_backfill._fed_meeting_dates")
    @patch("backend.signals.economic_core_1990_backfill.fetch_fred_observations")
    @patch("backend.signals.economic_core_1990_backfill.require_env", return_value="fred-key")
    def test_policy_rows_store_meetings_and_changes_only_not_daily_repeats(self, _env, fred, meetings, midpoint):
        fred.return_value = [
            {"date": "1990-02-07", "value": "8.25"},
            {"date": "1990-02-08", "value": "8.25"},
            {"date": "1990-03-27", "value": "8.25"},
            {"date": "2000-02-02", "value": "5.75"},
            {"date": "2000-03-21", "value": "6.0"},
        ]
        meetings.return_value = {date(1990, 2, 7), date(1990, 3, 27)}
        midpoint.return_value = [{
            "series_code": "US_POLICY_RATE_MID", "observation_date": "2008-12-16",
            "value": 0.125, "frequency": "E", "source": "DB:central_bank_policy_events",
        }]

        database = FakeDatabase()
        rows = _policy_rows(database, date(2026, 9, 13))

        self.assertEqual([row["observation_date"] for row in rows], [
            "1990-02-07", "1990-03-27", "2000-02-02", "2000-03-21", "2008-12-16",
        ])
        self.assertNotIn("1990-02-08", [row["observation_date"] for row in rows])
        self.assertEqual(rows[1]["value"], 8.25)
        midpoint.assert_called_once_with(database, date(2008, 12, 16), date(2026, 9, 13))

    def test_korean_spread_is_derived_only_on_matching_observation_dates(self):
        ten_year = [
            {"series_code": "KR10Y", "observation_date": "2003-01-02", "value": 5.2},
            {"series_code": "KR10Y", "observation_date": "2003-01-03", "value": 5.1},
        ]
        three_year = [
            {"series_code": "KR3Y", "observation_date": "2003-01-02", "value": 4.8},
        ]
        rows = _spread_rows(ten_year, three_year, "KR10Y3Y")
        self.assertEqual(rows, [{
            "series_code": "KR10Y3Y",
            "observation_date": "2003-01-02",
            "value": 0.4,
            "frequency": "D",
            "source": "DERIVED:KR10Y-KR3Y",
        }])


if __name__ == "__main__":
    unittest.main()
