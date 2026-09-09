import unittest
from datetime import date
from unittest.mock import patch

from backend.inflation_pipeline import fetch_bls_series, fetch_cleveland_nowcasts, policy_rows, save_automatic


class FakeSupabase:
    def __init__(self):
        self.upserts = []

    def request(self, method, table, **_kwargs):
        self.assert_request = (method, table)
        return [
            {"month": "2026-06-01", "status": "final"},
            {"month": "2026-07-01", "status": "provisional"},
        ]

    def upsert(self, table, rows, *, conflict):
        self.upserts.append((table, rows, conflict))


class InflationPipelineTests(unittest.TestCase):
    def test_policy_rows_carry_latest_ten_year_yield(self):
        fred = {
            "policy_rate": {date(2026, 9, 7): 5.5, date(2026, 9, 8): 5.5},
            "treasury_10y": {date(2026, 9, 7): 4.1},
        }
        rows = policy_rows(fred, date(2026, 9, 1), "2026-09-09T00:00:00Z")
        self.assertEqual(rows[-1]["treasury_10y_pct"], 4.1)
        self.assertEqual(rows[-1]["source"], "FRED:DFEDTARU,DGS10")

    @patch("backend.inflation_pipeline.requests.get")
    def test_nowcast_keeps_business_day_vintages_after_target_month(self, get):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [{
                    "chart": {"subcaption": "2026-08"},
                    "categories": [{"category": [{"label": "8/29"}, {"label": "9/2"}]}],
                    "dataset": [
                        {"seriesname": "CPI Inflation", "data": [{"value": "0.2"}, {"value": "0.3"}]},
                        {"seriesname": "PCE Inflation", "data": [{"value": "0.1"}, {"value": "0.2"}]},
                        {"seriesname": "Core CPI Inflation", "data": [{"value": "0.2"}, {"value": "0.3"}]},
                        {"seriesname": "Core PCE Inflation", "data": [{"value": "0.1"}, {"value": "0.2"}]},
                    ],
                }]

        get.return_value = Response()
        result = fetch_cleveland_nowcasts()
        target = date(2026, 8, 1)
        self.assertEqual(
            [point.observed_on for point in result["headline"][target]],
            [date(2026, 8, 29), date(2026, 9, 2)],
        )

    @patch("backend.inflation_pipeline.requests.post")
    def test_bls_history_is_fetched_in_public_api_year_blocks(self, post):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "status": "REQUEST_SUCCEEDED",
                    "Results": {"series": [{"data": [
                        {"year": "2026", "period": "M01", "value": "321.4"},
                        {"year": "2026", "period": "M13", "value": "999"},
                    ]}]},
                }

        post.return_value = Response()
        values = fetch_bls_series("CUSR0000SA0L12E", date(2026, 9, 9))
        self.assertEqual(values, {date(2026, 1, 1): 321.4})
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["startyear"], "2009")
        self.assertEqual(post.call_args_list[0].kwargs["json"]["endyear"], "2018")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["startyear"], "2019")

    def test_automatic_collection_preserves_confirmed_history(self):
        client = FakeSupabase()
        monthly = [
            {"month": "2026-06-01", "status": "final"},
            {"month": "2026-07-01", "status": "final"},
            {"month": "2026-08-01", "status": "provisional"},
        ]
        save_automatic(client, monthly)
        stored_months = [row["month"] for row in client.upserts[0][1]]
        self.assertEqual(stored_months, ["2026-07-01", "2026-08-01"])
        self.assertEqual(len(client.upserts), 1)


if __name__ == "__main__":
    unittest.main()
