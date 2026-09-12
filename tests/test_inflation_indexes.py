from __future__ import annotations

import unittest
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from inflation_pipeline import OFFICIAL_INDEX_SERIES, load_official_index_values
from sources.inflation_indexes import (
    ALL_SERIES_CODES,
    BEA_TABLE,
    ECOS_SERIES,
    fetch_bea_indexes,
    fetch_bls_indexes,
    fetch_ecos_indexes,
    fetch_kosis_indexes,
)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class InflationIndexSourceTests(unittest.TestCase):
    @patch("sources.inflation_indexes.request_with_retry")
    def test_bls_batches_four_series_and_ignores_annual_average(self, request):
        request.return_value = Response({
            "status": "REQUEST_SUCCEEDED",
            "Results": {"series": [
                {"seriesID": series_id, "data": [
                    {"year": "2026", "period": "M08", "value": "123.45"},
                    {"year": "2026", "period": "M13", "value": "999"},
                ]}
                for series_id in ("CUSR0000SA0", "CUSR0000SA0L1E", "WPSFD49501", "WPSFD49511")
            ]},
        })
        result = fetch_bls_indexes(date(2026, 1, 1), date(2026, 9, 1))
        self.assertEqual(set(result), {"US_CPI", "US_CORE_CPI", "US_PPI", "US_CORE_PPI"})
        self.assertTrue(all(len(rows) == 1 for rows in result.values()))

    @patch("sources.inflation_indexes.request_with_retry")
    def test_bea_selects_table_lines_one_and_twenty_five(self, request):
        request.return_value = Response({"BEAAPI": {"Results": {"Data": [
            {"LineNumber": "1", "TimePeriod": "2026M08", "DataValue": "125.4"},
            {"LineNumber": "25", "TimePeriod": "2026M08", "DataValue": "124.1"},
            {"LineNumber": "2", "TimePeriod": "2026M08", "DataValue": "999"},
        ]}}})
        result = fetch_bea_indexes(date(2026, 8, 1), date(2026, 8, 1), "key")
        self.assertEqual(result["US_PCE"][0]["value"], 125.4)
        self.assertEqual(result["US_CORE_PCE"][0]["value"], 124.1)
        self.assertEqual(BEA_TABLE, "T20804")

    @patch("sources.inflation_indexes.request_with_retry")
    def test_kosis_resolves_exact_tables_and_keeps_national_monthly_index(self, request):
        request.side_effect = [
            Response([{"ORG_ID": "101", "TBL_ID": "HEAD", "TBL_NM": "소비자물가지수(2020=100)"}]),
            Response([{"PRD_DE": "202608", "DT": "118.20", "C1_NM": "전국", "ITM_NM": "총지수"}]),
            Response([{"ORG_ID": "101", "TBL_ID": "CORE", "TBL_NM": "식료품 및 에너지제외지수(2020=100)"}]),
            Response([{"PRD_DE": "202608", "DT": "117.30", "C1_NM": "전국", "ITM_NM": "지수"}]),
        ]
        result = fetch_kosis_indexes(date(2026, 8, 1), date(2026, 8, 1), "key")
        self.assertEqual(result["KR_CPI"][0]["source"], "KOSIS:101/HEAD")
        self.assertEqual(result["KR_CORE_CPI"][0]["value"], 117.3)

    @patch("sources.inflation_indexes.request_with_retry")
    def test_ecos_reads_headline_ppi_and_import_price_indexes(self, request):
        request.side_effect = [
            Response({"StatisticSearch": {"row": [{"TIME": "202608", "DATA_VALUE": "121.1"}]}}),
            Response({"StatisticSearch": {"row": [{"TIME": "202608", "DATA_VALUE": "132.2"}]}}),
        ]
        result = fetch_ecos_indexes(date(2026, 8, 1), date(2026, 8, 1), "key")
        self.assertEqual(result["KR_PPI"][0]["value"], 121.1)
        self.assertEqual(result["KR_IMPORT_PRICE"][0]["value"], 132.2)
        self.assertEqual(ECOS_SERIES["KR_IMPORT_PRICE"], ("401Y015", "*AA", "W"))
        self.assertEqual(result["KR_IMPORT_PRICE"][0]["source"], "ECOS:401Y015/*AA/W")

    def test_integrated_model_reads_six_official_indexes_from_chart_storage(self):
        class Database:
            def request(self, _method, _table, *, params):
                code = params["series_code"].removeprefix("eq.")
                return [{"observation_date": "2026-08-01", "value": len(code)}]

        result = load_official_index_values(Database(), date(2026, 1, 1))
        self.assertEqual(set(result), set(OFFICIAL_INDEX_SERIES))
        self.assertEqual(len(OFFICIAL_INDEX_SERIES), 6)

    def test_automatic_and_backfill_are_separate_and_workflow_is_single(self):
        automatic = (ROOT / "backend/signals/inflation_index_automatic.py").read_text(encoding="utf-8")
        backfill = (ROOT / "backend/signals/inflation_index_backfill.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/economic-chart-data.yml").read_text(encoding="utf-8")
        manual = (ROOT / ".github/workflows/inflation-index-backfill.yml").read_text(encoding="utf-8")
        self.assertIn("AUTOMATIC_MONTHLY_PERIODS - 1", automatic)
        self.assertNotIn("inflation_index_backfill", automatic)
        self.assertIn("end.year - 20", backfill)
        self.assertEqual(len(ALL_SERIES_CODES), 10)
        self.assertIn("signals.inflation_index_automatic", workflow)
        self.assertIn("python -m inflation_pipeline", workflow)
        self.assertNotIn("schedule:", manual)


if __name__ == "__main__":
    unittest.main()
