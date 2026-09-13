import unittest
from datetime import date
from unittest.mock import patch

from backend.sources.policy_rates import (
    KR_POLICY_ITEM_CODE,
    KR_POLICY_STAT_CODE,
    fetch_korea_policy_rate_rows,
    fetch_us_policy_rate_chart_rows,
)
from backend.signals import policy_rate_automatic, policy_rate_backfill


class PolicyRateSourceTests(unittest.TestCase):
    class SavedEventDatabase:
        def __init__(self, events):
            self.events = events
            self.requests = []

        def request(self, method, table, **kwargs):
            self.requests.append((method, table, kwargs))
            if method == "GET" and table == "central_bank_policy_events":
                return self.events
            return []

    @patch("backend.sources.policy_rates._fetch_fed_page")
    @patch("backend.sources.policy_rates._fed_decision_links")
    def test_us_rate_prefers_saved_fomc_ranges_and_fetches_only_missing_statement(self, links, fetch_page):
        links.return_value = {
            date(2026, 1, 28): "https://fed.example/20260128",
            date(2026, 3, 18): "https://fed.example/20260318",
        }
        fetch_page.return_value = "<p>The Committee decided to maintain the target range for the federal funds rate at 4 to 4-1/4 percent.</p>"
        database = self.SavedEventDatabase([{
            "meeting_date": "2026-01-28",
            "target_range_lower": "4.25",
            "target_range_upper": "4.50",
        }])

        rows = fetch_us_policy_rate_chart_rows(
            database, date(2026, 1, 1), date(2026, 3, 31), fill_missing_from_fed=True,
        )

        self.assertEqual(rows, [
            {"series_code": "US_POLICY_RATE_MID", "observation_date": "2026-01-28", "value": 4.375, "frequency": "E", "source": "DB:central_bank_policy_events"},
            {"series_code": "US_POLICY_RATE_MID", "observation_date": "2026-03-18", "value": 4.125, "frequency": "E", "source": "FED:FOMC-statement"},
        ])
        fetch_page.assert_called_once_with("https://fed.example/20260318")

    @patch("backend.sources.policy_rates._fed_decision_links")
    def test_regular_collection_reads_saved_events_without_requesting_fed_history(self, links):
        database = self.SavedEventDatabase([{
            "meeting_date": "2026-01-28",
            "target_range_lower": "4.25",
            "target_range_upper": "4.50",
        }])
        rows = fetch_us_policy_rate_chart_rows(database, date(2026, 1, 1), date(2026, 3, 31))
        self.assertEqual([row["observation_date"] for row in rows], ["2026-01-28"])
        links.assert_not_called()

    def test_fed_statement_parser_accepts_historical_fraction_only_range_bound(self):
        from backend.sources.policy_rates import _fed_target_range
        html = "<p>The Committee decided to keep its target range for the federal funds rate at 0 to 1/4 percent.</p>"
        self.assertEqual(_fed_target_range(html), (0.0, 0.25))

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
    @patch("backend.signals.policy_rate_automatic.fetch_us_policy_rate_chart_rows")
    def test_automatic_collection_writes_only_decision_date_chart_values(self, us_source, kr_source):
        us_source.return_value = [{
            "series_code": "US_POLICY_RATE_MID", "observation_date": "2026-09-17", "value": 4.125,
            "frequency": "E", "source": "DB:central_bank_policy_events",
        }]
        kr_source.return_value = [{
            "series_code": "KR_POLICY_RATE", "observation_date": "2026-09-01", "value": 2.5, "frequency": "M", "source": "ECOS:722Y001/0101000",
        }]
        database = self.FakeDatabase()
        counts = policy_rate_automatic.collect(date(2026, 9, 18), database)
        self.assertEqual(database.upserts[0][1][0]["observation_date"], "2026-09-17")
        self.assertEqual(database.upserts[0][1][0]["frequency"], "E")
        self.assertEqual(counts, {"US_POLICY_RATE_MID": 1, "KR_POLICY_RATE": 1})
        us_source.assert_called_once_with(database, date(2026, 5, 21), date(2026, 9, 18))

    @patch("backend.signals.policy_rate_backfill.fetch_us_policy_rate_chart_rows", side_effect=RuntimeError("Fed records unavailable"))
    def test_backfill_does_not_write_when_us_decision_source_fails(self, _source):
        database = self.FakeDatabase()
        with self.assertRaisesRegex(RuntimeError, "Fed records unavailable"):
            policy_rate_backfill.backfill(date(2026, 9, 13), database)
        self.assertEqual(database.upserts, [])
        self.assertEqual(database.requests, [])


if __name__ == "__main__":
    unittest.main()
