import unittest
from datetime import date
from unittest.mock import patch

from backend.sources.policy_rates import (
    KR_POLICY_ITEM_CODE,
    KR_POLICY_STAT_CODE,
    fetch_fed_decision_dates,
    fetch_korea_policy_rate_rows,
    fetch_us_policy_rate_rows,
    us_policy_event_chart_rows,
)
from backend.signals import policy_rate_automatic, policy_rate_backfill


class PolicyRateSourceTests(unittest.TestCase):
    @patch("backend.sources.policy_rates.fetch_fred_observations")
    def test_us_range_rows_store_both_bounds_and_midpoint(self, observations):
        observations.side_effect = [
            [
                {"date": "2026-01-01", "value": "4.25"},
                {"date": "2026-01-02", "value": "4.25"},
                {"date": "2026-01-03", "value": "4.00"},
            ],
            [
                {"date": "2026-01-01", "value": "4.50"},
                {"date": "2026-01-02", "value": "4.50"},
                {"date": "2026-01-03", "value": "4.25"},
            ],
        ]
        rows = fetch_us_policy_rate_rows(date(2026, 1, 1), date(2026, 1, 3), "fred-key")
        self.assertEqual(rows[0]["target_lower_pct"], 4.25)
        self.assertEqual(rows[0]["target_upper_pct"], 4.5)
        self.assertEqual(rows[0]["target_mid_pct"], 4.375)
        self.assertEqual(rows[-1]["target_mid_pct"], 4.125)

        chart_rows = us_policy_event_chart_rows(rows, [date(2026, 1, 1), date(2026, 1, 2)])
        self.assertEqual([row["observation_date"] for row in chart_rows], ["2026-01-01", "2026-01-02"])
        self.assertEqual([row["value"] for row in chart_rows], [4.375, 4.375])

    @patch("backend.sources.policy_rates.requests.get")
    def test_fed_statement_pages_supply_decision_dates_including_holds(self, get):
        class Response:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

        get.side_effect = [
            Response('<a href="/newsevents/pressreleases/monetary20260128a.htm">Statement</a>'),
            Response('<a href="/newsevents/pressreleases/monetary20090128a.htm">Statement</a>'),
        ]
        dates = fetch_fed_decision_dates(date(2009, 1, 1), date(2009, 12, 31))
        self.assertEqual(dates, [date(2009, 1, 28)])

    @patch("backend.sources.policy_rates.requests.get")
    def test_korea_monthly_rate_uses_ecos_base_rate_item(self, get):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"StatisticSearch": {"row": [
                    {"TIME": "202601", "DATA_VALUE": "2.75"},
                    {"TIME": "202602", "DATA_VALUE": "2.50"},
                ]}}

        get.return_value = Response()
        rows = fetch_korea_policy_rate_rows(date(2026, 1, 1), date(2026, 2, 1), "ecos-key")
        self.assertEqual(KR_POLICY_STAT_CODE, "722Y001")
        self.assertEqual(KR_POLICY_ITEM_CODE, "0101000")
        self.assertEqual(rows, [
            {"series_code": "KR_POLICY_RATE", "observation_date": "2026-01-01", "value": 2.75, "frequency": "M", "source": "ECOS:722Y001/0101000"},
            {"series_code": "KR_POLICY_RATE", "observation_date": "2026-02-01", "value": 2.5, "frequency": "M", "source": "ECOS:722Y001/0101000"},
        ])


class PolicyRateCollectionTests(unittest.TestCase):
    class FakeDatabase:
        def __init__(self):
            self.upserts = []
            self.requests = []

        def upsert(self, table, rows, *, conflict):
            self.upserts.append((table, rows, conflict))

        def request(self, method, table, **kwargs):
            self.requests.append((method, table, kwargs))
            return []

    @patch("backend.signals.policy_rate_automatic.fetch_korea_policy_rate_rows")
    @patch("backend.signals.policy_rate_automatic.fetch_fed_decision_dates")
    @patch("backend.signals.policy_rate_automatic.fetch_us_policy_rate_rows")
    def test_automatic_collection_writes_decision_date_chart_values(self, us_source, decision_dates, kr_source):
        us_source.return_value = [
            {"observed_on": "2026-03-01", "target_lower_pct": 4.25, "target_upper_pct": 4.5, "target_mid_pct": 4.375, "source": "FRED:DFEDTARL,DFEDTARU"},
            {"observed_on": "2026-06-01", "target_lower_pct": 4.25, "target_upper_pct": 4.5, "target_mid_pct": 4.375, "source": "FRED:DFEDTARL,DFEDTARU"},
            {"observed_on": "2026-09-01", "target_lower_pct": 4.0, "target_upper_pct": 4.25, "target_mid_pct": 4.125, "source": "FRED:DFEDTARL,DFEDTARU"},
        ]
        decision_dates.return_value = [date(2026, 6, 1), date(2026, 9, 1)]
        kr_source.return_value = [{
            "series_code": "KR_POLICY_RATE", "observation_date": "2026-09-01", "value": 2.5, "frequency": "M", "source": "ECOS:722Y001/0101000",
        }]
        database = self.FakeDatabase()
        counts = policy_rate_automatic.collect(date(2026, 9, 13), database)
        chart_write = database.upserts[0]
        self.assertEqual(chart_write[0], "economic_chart_points")
        self.assertEqual([row["observation_date"] for row in chart_write[1]], ["2026-06-01", "2026-09-01"])
        self.assertEqual([row["value"] for row in chart_write[1]], [4.375, 4.125])
        self.assertEqual(counts, {"US_POLICY_RATE_MID": 2, "KR_POLICY_RATE": 1})

    @patch("backend.signals.policy_rate_backfill.fetch_us_policy_rate_rows", side_effect=RuntimeError("FRED unavailable"))
    def test_backfill_does_not_write_when_first_source_fails(self, _source):
        database = self.FakeDatabase()
        with self.assertRaisesRegex(RuntimeError, "FRED unavailable"):
            policy_rate_backfill.backfill(date(2026, 9, 13), database)
        self.assertEqual(database.upserts, [])
        self.assertEqual(database.requests, [])


if __name__ == "__main__":
    unittest.main()
