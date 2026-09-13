from __future__ import annotations

import unittest
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from inflation_pipeline import OFFICIAL_INFLATION_SERIES, load_canonical_series
from signals.inflation_rate_automatic import collect as collect_automatic_rates
from sources.inflation_rates import (
    ALL_SERIES_CODES,
    BEA_TABLE,
    ECOS_SERIES,
    fetch_bea_rates,
    fetch_bls_rates,
    fetch_ecos_rates,
    fetch_kosis_rates,
    _ecos_month_windows,
    _kosis_month_windows,
)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class InflationRateSourceTests(unittest.TestCase):
    @patch("sources.inflation_rates.request_with_retry")
    def test_bls_returns_yoy_rates_and_ignores_annual_average(self, request):
        request.return_value = Response({
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": [
                {"seriesID": series_id, "data": [
                    {"year": "2026", "period": "M08", "value": "105"},
                    {"year": "2025", "period": "M08", "value": "100"},
                    {"year": "2026", "period": "M13", "value": "999"},
                ]}
                for series_id in ("CUSR0000SA0", "CUSR0000SA0L1E", "WPSFD49501", "WPSFD49511")
            ]},
        })
        result = fetch_bls_rates(date(2026, 1, 1), date(2026, 9, 1))
        self.assertEqual(set(result), {"US_CPI", "US_CORE_CPI", "US_PPI", "US_CORE_PPI"})
        self.assertTrue(all(len(rows) == 1 for rows in result.values()))
        self.assertTrue(all(rows[0]["value"] == 5 for rows in result.values()))
        self.assertTrue(all(rows[0]["source"].endswith(":YoY") for rows in result.values()))

    @patch("sources.inflation_rates.request_with_retry")
    def test_bea_selects_lines_and_returns_yoy_rates(self, request):
        request.return_value = Response({"BEAAPI": {"Results": {"Data": [
            {"LineNumber": "1", "TimePeriod": "2025M08", "DataValue": "100"},
            {"LineNumber": "1", "TimePeriod": "2026M08", "DataValue": "103.7"},
            {"LineNumber": "25", "TimePeriod": "2025M08", "DataValue": "100"},
            {"LineNumber": "25", "TimePeriod": "2026M08", "DataValue": "102.9"},
            {"LineNumber": "2", "TimePeriod": "2026M08", "DataValue": "999"},
        ]}}})
        result = fetch_bea_rates(date(2026, 8, 1), date(2026, 8, 1), "key")
        self.assertAlmostEqual(result["US_PCE"][0]["value"], 3.7)
        self.assertAlmostEqual(result["US_CORE_PCE"][0]["value"], 2.9)
        self.assertEqual(BEA_TABLE, "T20804")

    @patch("sources.inflation_rates.requests.get")
    @patch("sources.inflation_rates.request_with_retry", side_effect=lambda operation: operation())
    def test_kosis_reads_headline_and_food_energy_excluded_yoy_directly(self, request, get):
        get.side_effect = [
            Response([{"PRD_DE": "202608", "DT": "3.7"}]),
            Response([{"PRD_DE": "202608", "DT": "2.9"}]),
        ]
        result = fetch_kosis_rates(date(2026, 8, 1), date(2026, 8, 1), "key")
        self.assertEqual(result["KR_CPI"][0]["source"], "KOSIS:101/DT_1J22042:YoY")
        self.assertEqual(result["KR_CPI"][0]["value"], 3.7)
        self.assertEqual(result["KR_CORE_CPI"][0]["value"], 2.9)
        params = [call.kwargs["params"] for call in get.call_args_list]
        self.assertEqual([item["itmId"] for item in params], ["T03", "T03"])
        self.assertEqual([item["objL1"] for item in params], ["0", "4"])
        self.assertTrue(all("objL2" not in item for item in params))

    def test_kosis_long_history_is_split_into_bounded_windows(self):
        self.assertEqual(
            _kosis_month_windows(date(2006, 9, 1), date(2016, 9, 1)),
            [(date(2006, 9, 1), date(2011, 8, 1)), (date(2011, 9, 1), date(2016, 8, 1)), (date(2016, 9, 1), date(2016, 9, 1))],
        )

    def test_ecos_long_history_is_split_without_splitting_automatic_windows(self):
        self.assertEqual(
            _ecos_month_windows(date(2005, 9, 1), date(2026, 9, 1)),
            [(date(2005, 9, 1), date(2015, 8, 1)), (date(2015, 9, 1), date(2025, 8, 1)), (date(2025, 9, 1), date(2026, 9, 1))],
        )
        self.assertEqual(_ecos_month_windows(date(2026, 5, 1), date(2026, 9, 1)), [(date(2026, 5, 1), date(2026, 9, 1))])

    @patch("sources.inflation_rates.request_with_retry")
    def test_ecos_calculates_yoy_without_returning_raw_levels(self, request):
        request.side_effect = [
            Response({"StatisticSearch": {"row": [
                {"TIME": "202508", "DATA_VALUE": "100"}, {"TIME": "202608", "DATA_VALUE": "102.4"},
            ]}}),
            Response({"StatisticSearch": {"row": [
                {"TIME": "202508", "DATA_VALUE": "100"}, {"TIME": "202608", "DATA_VALUE": "105.1"},
            ]}}),
        ]
        result = fetch_ecos_rates(date(2026, 8, 1), date(2026, 8, 1), "key")
        self.assertAlmostEqual(result["KR_PPI"][0]["value"], 2.4)
        self.assertAlmostEqual(result["KR_IMPORT_PRICE"][0]["value"], 5.1)
        self.assertEqual(ECOS_SERIES["KR_IMPORT_PRICE"], ("401Y015", "*AA", "W"))
        self.assertEqual(result["KR_IMPORT_PRICE"][0]["source"], "ECOS:401Y015/*AA/W:YoY")

    def test_inflation_pipeline_reads_six_official_rates_from_canonical_storage(self):
        class Database:
            def request(self, _method, _table, *, params):
                code = params["series_code"].removeprefix("eq.")
                return [{"observation_date": "2026-08-01", "value": len(code)}]

        result = load_canonical_series(Database(), tuple(OFFICIAL_INFLATION_SERIES.values()))
        self.assertEqual(set(result), set(OFFICIAL_INFLATION_SERIES.values()))
        self.assertEqual(len(OFFICIAL_INFLATION_SERIES), 6)

    @patch("signals.inflation_rate_automatic.fetch_all_rates")
    def test_automatic_upserts_recent_rates_so_official_revisions_are_applied(self, fetch):
        rows = [{
            "series_code": "US_CPI", "observation_date": "2026-08-01",
            "value": 3.7, "frequency": "M", "source": "BLS:CUSR0000SA0:YoY",
        }]
        fetch.return_value = {"US_CPI": rows}

        class Database:
            def __init__(self):
                self.calls = []

            def upsert(self, table, payload, *, conflict):
                self.calls.append((table, payload, conflict))

        database = Database()
        result = collect_automatic_rates(date(2026, 9, 13), database)
        self.assertEqual(result, {"US_CPI": 1})
        self.assertEqual(database.calls, [("economic_chart_points", rows, "series_code,observation_date")])
        fetch.assert_called_once_with(date(2026, 4, 1), date(2026, 8, 1))

    @patch("signals.inflation_rate_backfill.fetch_all_rates")
    def test_backfill_ends_at_last_completed_month(self, fetch):
        from signals.inflation_rate_backfill import backfill

        fetch.return_value = {code: [{
            "series_code": code,
            "observation_date": "2026-08-01",
            "value": 1.0,
            "frequency": "M",
            "source": "test",
        }] for code in ALL_SERIES_CODES}

        class Database:
            def upsert(self, *_args, **_kwargs):
                return None

            def request(self, *_args, **_kwargs):
                return []

        backfill(date(2026, 9, 13), Database())
        fetch.assert_called_once_with(date(2006, 8, 1), date(2026, 8, 1))

    def test_automatic_and_backfill_are_separate_and_workflow_is_single(self):
        automatic = (ROOT / "backend/signals/inflation_rate_automatic.py").read_text(encoding="utf-8")
        backfill = (ROOT / "backend/signals/inflation_rate_backfill.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/economic-chart-data.yml").read_text(encoding="utf-8")
        manual = (ROOT / ".github/workflows/inflation-rate-backfill.yml").read_text(encoding="utf-8")
        self.assertIn("AUTOMATIC_MONTHLY_PERIODS - 1", automatic)
        self.assertNotIn("inflation_rate_backfill", automatic)
        self.assertIn("end.year - 20", backfill)
        self.assertEqual(len(ALL_SERIES_CODES), 10)
        self.assertIn("signals.inflation_rate_automatic", workflow)
        self.assertIn("python -m inflation_pipeline", workflow)
        self.assertNotIn("schedule:", manual)


if __name__ == "__main__":
    unittest.main()
