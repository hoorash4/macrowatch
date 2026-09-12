from __future__ import annotations

import math
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals import economic_chart_automatic as automatic  # noqa: E402
from signals import economic_chart_pipeline as pipeline  # noqa: E402
from sources import krx_index_fundamentals as krx  # noqa: E402


class FakeFrame:
    def __init__(self, rows, columns=("PER", "PBR")):
        self._rows = rows
        self.columns = list(columns)
        self.empty = not rows

    def iterrows(self):
        return iter(self._rows)


class FakeDb:
    def __init__(self, existing=None):
        self.existing = existing or {}
        self.upserts = []

    def request(self, method, table, params=None, **_kwargs):
        if method == "GET" and table == pipeline.TABLE:
            code = str((params or {}).get("series_code", "")).removeprefix("eq.")
            return [{"observation_date": item} for item in self.existing.get(code, set())]
        if method == "GET" and table == "targets":
            return []
        raise AssertionError((method, table, params))

    def upsert(self, table, rows, conflict):
        self.upserts.append((table, list(rows), conflict))


class KospiValuationSourceTests(unittest.TestCase):
    def test_valid_per_and_sub_one_pbr_are_accepted(self):
        frame = FakeFrame([("2026-09-11", {"PER": "14.25", "PBR": "0.84"})])
        rows = krx._parse_frame(frame, required_date=date(2026, 9, 11))
        self.assertEqual(rows["KOSPI_PER"][0]["value"], 14.25)
        self.assertEqual(rows["KOSPI_PBR"][0]["value"], 0.84)

    def test_empty_response_fails(self):
        with self.assertRaisesRegex(krx.KrxResponseError, "비어"):
            krx._parse_frame(FakeFrame([]))

    def test_missing_per_or_pbr_column_fails(self):
        for columns in (("PBR",), ("PER",)):
            with self.subTest(columns=columns):
                with self.assertRaisesRegex(krx.KrxResponseError, "컬럼 누락"):
                    krx._parse_frame(FakeFrame([("2026-09-11", {"PER": 10, "PBR": 1})], columns))

    def test_null_nan_blank_and_zero_values_fail(self):
        invalid_cases = [
            {"PER": None, "PBR": 1.0},
            {"PER": math.nan, "PBR": 1.0},
            {"PER": "", "PBR": 1.0},
            {"PER": 0, "PBR": 1.0},
            {"PER": 10.0, "PBR": None},
            {"PER": 10.0, "PBR": math.nan},
            {"PER": 10.0, "PBR": ""},
            {"PER": 10.0, "PBR": 0},
        ]
        for values in invalid_cases:
            with self.subTest(values=values):
                with self.assertRaises(krx.KrxResponseError):
                    krx._parse_frame(FakeFrame([("2026-09-11", values)]))

    def test_requested_day_must_exist(self):
        frame = FakeFrame([("2026-09-10", {"PER": 10.0, "PBR": 0.9})])
        with self.assertRaisesRegex(krx.KrxResponseError, "날짜가 응답에 없습니다"):
            krx._parse_frame(frame, required_date=date(2026, 9, 11))

    def test_network_error_is_distinguished_from_structure_error(self):
        network_getter = Mock(side_effect=krx.requests.Timeout("timeout"))
        with patch.object(krx.stock, "get_index_fundamental_by_date", network_getter, create=True):
            with self.assertRaisesRegex(krx.KrxNetworkError, "네트워크"):
                krx.fetch_krx_kospi_fundamental_rows(date(2026, 9, 11), date(2026, 9, 11))
        structure_getter = Mock(side_effect=KeyError("changed"))
        with patch.object(krx.stock, "get_index_fundamental_by_date", structure_getter, create=True):
            with self.assertRaisesRegex(krx.KrxResponseError, "구조 변경"):
                krx.fetch_krx_kospi_fundamental_rows(date(2026, 9, 11), date(2026, 9, 11))

    def test_market_holiday_detection_is_exact(self):
        with patch.object(krx.stock, "get_nearest_business_day_in_a_week", return_value="20260911", create=True):
            self.assertTrue(krx.is_krx_business_day(date(2026, 9, 11)))
        with patch.object(krx.stock, "get_nearest_business_day_in_a_week", return_value="20260910", create=True):
            self.assertFalse(krx.is_krx_business_day(date(2026, 9, 11)))
        self.assertFalse(krx.is_krx_business_day(date(2026, 9, 12)))


class KospiValuationStorageTests(unittest.TestCase):
    def test_duplicate_dates_are_not_written(self):
        db = FakeDb(existing={"KOSPI_PER": {"2026-09-10"}})
        rows = [
            {"series_code": "KOSPI_PER", "observation_date": "2026-09-10", "value": 12.0, "frequency": "D", "source": "x"},
            {"series_code": "KOSPI_PER", "observation_date": "2026-09-11", "value": 12.1, "frequency": "D", "source": "x"},
        ]
        self.assertEqual(pipeline._insert_missing(db, rows, date(2026, 9, 1)), 1)
        self.assertEqual(db.upserts[0][1][0]["observation_date"], "2026-09-11")

    def test_holiday_skips_without_fetch_or_write(self):
        db = FakeDb()
        with (
            patch.object(automatic, "is_krx_business_day", return_value=False),
            patch.object(automatic, "fetch_krx_kospi_fundamental_day") as fetch,
        ):
            result = automatic.collect_kospi_valuation(date(2026, 9, 11), db)
        self.assertEqual(result, {"KOSPI_PER": 0, "KOSPI_PBR": 0})
        fetch.assert_not_called()
        self.assertEqual(db.upserts, [])

    def test_latest_failure_propagates_and_preserves_existing_data(self):
        db = FakeDb(existing={"KOSPI_PER": {"2026-09-10"}, "KOSPI_PBR": {"2026-09-10"}})
        with (
            patch.object(automatic, "is_krx_business_day", return_value=True),
            patch.object(automatic, "fetch_krx_kospi_fundamental_day", side_effect=krx.KrxResponseError("bad response")),
        ):
            with self.assertRaises(krx.KrxResponseError):
                automatic.collect_kospi_valuation(date(2026, 9, 11), db)
        self.assertEqual(db.upserts, [])


class KospiValuationContractTests(unittest.TestCase):
    def test_kospi_valuation_is_integrated_into_the_single_0730_refresh(self):
        workflow = (ROOT / ".github/workflows/economic-chart-data.yml").read_text(encoding="utf-8")
        automatic_source = (ROOT / "backend/signals/economic_chart_automatic.py").read_text(encoding="utf-8")
        self.assertEqual(workflow.count('cron: "30 22 * * *"'), 1)
        self.assertNotIn('cron: "20 7 * * 1-5"', workflow)
        self.assertNotIn("--phase", workflow)
        self.assertIn("inserted.update(collect_kospi_valuation(db=db))", automatic_source)
        self.assertIn("fetch_krx_kospi_fundamental_day(target)", automatic_source)
        self.assertIn("is_krx_business_day(target)", automatic_source)
        self.assertIn("KRX_ID: ${{ secrets.KRX_ID }}", workflow)
        self.assertIn("KRX_PW: ${{ secrets.KRX_PW }}", workflow)

    def test_frontend_per_pbr_and_existing_moving_averages_are_unchanged(self):
        chart = (ROOT / "assets/js/charts/economic-charts.js").read_text(encoding="utf-8")
        self.assertIn("KOSPI_PER", chart)
        self.assertIn("KOSPI_PBR", chart)
        self.assertIn("D:[5,20,'5일','20일']", chart)
        self.assertNotRegex(chart.lower(), r"forward[_ -]?per|선행\s*per")

    def test_only_kospi_not_kosdaq_and_pykrx_is_pinned(self):
        source = (ROOT / "backend/sources/krx_index_fundamentals.py").read_text(encoding="utf-8")
        requirements = (ROOT / "backend/requirements.txt").read_text(encoding="utf-8")
        self.assertIn('KOSPI_INDEX_TICKER = "1001"', source)
        self.assertNotIn("KOSDAQ", source)
        self.assertIn("pykrx==1.2.8", requirements)

    def test_backfill_runtime_is_retired(self):
        self.assertFalse((ROOT / ".github/workflows/economic-chart-backfill-once.yml").exists())
        self.assertFalse((ROOT / "backend/signals/economic_chart_backfill.py").exists())

    def test_existing_failure_email_covers_economic_chart_schedule(self):
        notifier = (ROOT / ".github/workflows/scheduled-failure-email.yml").read_text(encoding="utf-8")
        self.assertIn('"Refresh economic chart data"', notifier)
        self.assertIn("github.event.workflow_run.event == 'schedule'", notifier)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", notifier)
        self.assertFalse((ROOT / ".github/workflows/patch-stale-foreign-flow-test.yml").exists())


if __name__ == "__main__":
    unittest.main()
