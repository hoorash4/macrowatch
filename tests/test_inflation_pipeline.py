import unittest
from datetime import date
from unittest.mock import patch

from backend.inflation_pipeline import fetch_bls_series, save_automatic


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
        daily = [{"observed_on": "2026-09-08"}]
        save_automatic(client, monthly, daily)
        stored_months = [row["month"] for row in client.upserts[0][1]]
        self.assertEqual(stored_months, ["2026-07-01", "2026-08-01"])
        self.assertEqual(client.upserts[1], ("us_inflation_leading_daily", daily[-1], "observed_on"))


if __name__ == "__main__":
    unittest.main()
