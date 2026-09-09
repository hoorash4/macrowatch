import unittest

from backend.inflation_pipeline import save_automatic


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
