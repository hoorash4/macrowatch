import unittest
from datetime import date
from unittest.mock import patch

from backend.sources.policy_rates import (
    KR_POLICY_ITEM_CODE,
    KR_POLICY_STAT_CODE,
    fetch_korea_policy_rate_rows,
    fetch_us_policy_rate_chart_rows,
)
from backend.signals import policy_rate_automatic, policy_rate_chart_sync


class FakeDatabase:
    def __init__(self, events=None, chart_rows=None):
        self.events = events or []
        self.chart_rows = chart_rows or []
        self.upserts = []
        self.requests = []

    def upsert(self, table, rows, *, conflict):
        self.upserts.append((table, rows, conflict))

    def request(self, method, table, **kwargs):
        self.requests.append((method, table, kwargs))
        if method == "GET" and table == "central_bank_policy_events":
            return self.events
        if method == "GET" and table == "economic_chart_points":
            return self.chart_rows
        return []


class PolicyRateSourceTests(unittest.TestCase):
    def test_us_rate_projects_only_stored_decision_date_and_midpoint(self):
        database = FakeDatabase(events=[
            {"meeting_date": "2026-03-18", "target_range_lower": "4.00", "target_range_upper": "4.25"},
            {"meeting_date": "2026-01-28", "target_range_lower": "4.25", "target_range_upper": "4.50"},
        ])
        rows = fetch_us_policy_rate_chart_rows(
            database, date(2009, 1, 1), date(2026, 9, 13), recent_limit=5,
        )
        self.assertEqual(rows, [
            {"series_code": "US_POLICY_RATE_MID", "observation_date": "2026-01-28", "value": 4.375, "frequency": "E", "source": "DB:central_bank_policy_events"},
            {"series_code": "US_POLICY_RATE_MID", "observation_date": "2026-03-18", "value": 4.125, "frequency": "E", "source": "DB:central_bank_policy_events"},
        ])
        params = database.requests[0][2]["params"]
        self.assertEqual(params["order"], "meeting_date.desc")
        self.assertEqual(params["limit"], "5")

    def test_us_rate_rejects_completed_stored_event_with_missing_rate(self):
        database = FakeDatabase(events=[{
            "meeting_date": "2026-03-18", "target_range_lower": None, "target_range_upper": None,
        }])
        with self.assertRaisesRegex(RuntimeError, "2026-03-18"):
            fetch_us_policy_rate_chart_rows(database, date(2009, 1, 1), date(2026, 9, 13))

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
    @patch("backend.signals.policy_rate_automatic.fetch_us_policy_rate_chart_rows")
    def test_automatic_collection_checks_only_latest_five_stored_us_decisions(self, us_source):
        us_source.return_value = [{
            "series_code": "US_POLICY_RATE_MID", "observation_date": "2026-09-17", "value": 4.125,
            "frequency": "E", "source": "DB:central_bank_policy_events",
        }]
        database = FakeDatabase()
        count = policy_rate_automatic.collect_us(date(2026, 9, 18), database)
        us_source.assert_called_once_with(
            database, date(2009, 1, 1), date(2026, 9, 18), recent_limit=5,
        )
        self.assertEqual(database.upserts[0][1][0]["frequency"], "E")
        self.assertEqual(count, 1)

    @patch("backend.signals.policy_rate_chart_sync.fetch_us_policy_rate_chart_rows")
    def test_manual_sync_replaces_us_chart_rows_from_stored_events_only(self, source):
        source.return_value = [
            {"series_code": "US_POLICY_RATE_MID", "observation_date": "2009-01-28", "value": 0.125, "frequency": "E", "source": "DB:central_bank_policy_events"},
            {"series_code": "US_POLICY_RATE_MID", "observation_date": "2026-01-28", "value": 3.625, "frequency": "E", "source": "DB:central_bank_policy_events"},
        ]
        database = FakeDatabase(chart_rows=[
            {"observation_date": "2009-01-28"},
            {"observation_date": "2009-01-29"},
        ])
        result = policy_rate_chart_sync.sync(date(2026, 9, 13), database)
        self.assertEqual(result, {"rows": 2, "earliest": "2009-01-28", "latest": "2026-01-28"})
        deletes = [item for item in database.requests if item[0] == "DELETE"]
        self.assertEqual(deletes[0][2]["params"]["observation_date"], "eq.2009-01-29")

    @patch("backend.signals.policy_rate_chart_sync.fetch_us_policy_rate_chart_rows", return_value=[])
    def test_manual_sync_does_not_write_when_stored_history_is_missing(self, _source):
        database = FakeDatabase()
        with self.assertRaisesRegex(RuntimeError, "does not begin in 2009"):
            policy_rate_chart_sync.sync(date(2026, 9, 13), database)
        self.assertEqual(database.upserts, [])
        self.assertEqual(database.requests, [])


if __name__ == "__main__":
    unittest.main()
