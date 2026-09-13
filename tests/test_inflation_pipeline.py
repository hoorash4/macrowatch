import unittest
from datetime import date
from unittest.mock import patch

from backend.inflation_pipeline import (
    MODEL_VERSION, NowcastPoint, build_rows, fetch_cleveland_nowcasts,
    save_automatic, store_cleveland_nowcasts,
)


class FakeSupabase:
    def __init__(self): self.upserts = []
    def request(self, _method, _table, **_kwargs):
        return [{"month": "2026-06-01", "status": "final", "model_version": MODEL_VERSION},
                {"month": "2026-07-01", "status": "provisional", "model_version": "legacy"}]
    def upsert(self, table, rows, *, conflict): self.upserts.append((table, rows, conflict))


class InflationPipelineTests(unittest.TestCase):
    @patch("backend.inflation_pipeline.requests.get")
    def test_year_over_year_nowcast_keeps_latest_business_day_vintage(self, get):
        class Response:
            def raise_for_status(self): return None
            def json(self):
                return [{"chart": {"subcaption": "2026-08"},
                         "categories": [{"category": [{"label": "8/29"}, {"label": "9/2"}]}],
                         "dataset": [
                             {"seriesname": "CPI Inflation", "data": [{"value": "3.2"}, {"value": "3.3"}]},
                             {"seriesname": "PCE Inflation", "data": [{"value": "2.9"}, {"value": "3.0"}]},
                             {"seriesname": "Core CPI Inflation", "data": [{"value": "3.1"}, {"value": "3.2"}]},
                             {"seriesname": "Core PCE Inflation", "data": [{"value": "2.8"}, {"value": "2.9"}]},
                         ]}]
        get.return_value = Response()
        result = fetch_cleveland_nowcasts()
        self.assertEqual(result["headline"][date(2026, 8, 1)][-1], NowcastPoint(date(2026, 9, 2), 3.3, 3.0))

    def test_official_rows_replace_nowcast_and_provisional_uses_cleveland_yoy(self):
        july, august = date(2026, 7, 1), date(2026, 8, 1)
        official = {code: {july: value} for code, value in {
            "US_CPI": 3.0, "US_CORE_CPI": 3.2, "US_PCE": 2.5,
            "US_CORE_PCE": 2.8, "US_PPI": 2.0, "US_CORE_PPI": 2.2,
        }.items()}
        nowcasts = {"headline": {august: [NowcastPoint(date(2026, 9, 2), 3.4, 3.0)]},
                    "core": {august: [NowcastPoint(date(2026, 9, 2), 3.1, 2.9)]}}
        rows = build_rows(official, nowcasts, {date(2026, 7, 1): 4.0})
        self.assertEqual([row["status"] for row in rows], ["final", "provisional"])
        self.assertAlmostEqual(rows[0]["headline_yoy_pct"], 2.6)
        self.assertAlmostEqual(rows[1]["headline_yoy_pct"], 3.02)

    def test_automatic_collection_preserves_unchanged_final_history(self):
        client = FakeSupabase()
        rows = [{"month": "2026-06-01", "status": "final", "model_version": MODEL_VERSION},
                {"month": "2026-07-01", "status": "final", "model_version": MODEL_VERSION},
                {"month": "2026-08-01", "status": "provisional", "model_version": MODEL_VERSION}]
        save_automatic(client, rows)
        self.assertEqual([row["month"] for row in client.upserts[0][1]], ["2026-07-01", "2026-08-01"])

    def test_automatic_nowcast_storage_keeps_only_five_recent_target_months(self):
        client = FakeSupabase()
        months = [date(2026, month, 1) for month in range(1, 7)]
        nowcasts = {
            kind: {month: [NowcastPoint(month, 3.0, 2.5)] for month in months}
            for kind in ("headline", "core")
        }
        self.assertEqual(store_cleveland_nowcasts(client, nowcasts), 10)
        stored_months = {row["target_month"] for row in client.upserts[0][1]}
        self.assertEqual(stored_months, {month.isoformat() for month in months[-5:]})


if __name__ == "__main__": unittest.main()
