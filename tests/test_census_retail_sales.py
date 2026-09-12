from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources import census_retail_sales as census  # noqa: E402


class CensusRetailSalesTests(unittest.TestCase):
    def test_parser_keeps_only_seasonally_adjusted_total_retail_sales(self):
        payload = [
            ["cell_value", "category_code", "data_type_code", "seasonally_adj", "time"],
            ["700,123", "44X72", "SM", "yes", "2026-07"],
            ["690000", "44X72", "SM", "no", "2026-07"],
            ["4.0", "44X72", "MPCSM", "yes", "2026-07"],
            ["123", "441", "SM", "yes", "2026-07"],
            ["NA", "44X72", "SM", "yes", "2026-08"],
        ]
        self.assertEqual(census.parse_marts_payload(payload), {date(2026, 7, 1): 700123.0})

    def test_yoy_uses_same_month_prior_year_and_skips_missing_baselines(self):
        values = {
            date(2025, 6, 1): 100.0,
            date(2025, 7, 1): 200.0,
            date(2026, 6, 1): 110.0,
            date(2026, 7, 1): 230.0,
            date(2026, 8, 1): 250.0,
        }
        self.assertEqual(census.yoy_values(values), {
            date(2026, 6, 1): 10.0,
            date(2026, 7, 1): 15.0,
        })

    def test_fetch_uses_extra_comparison_year_and_returns_yoy(self):
        response_2025 = Mock()
        response_2025.raise_for_status = Mock()
        response_2025.json.return_value = [
            ["cell_value", "time_slot_id", "time_slot_date", "error_data", "category_code", "seasonally_adj", "data_type_code", "time"],
            ["700000", "1", "2025-07-01", "no", "44X72", "yes", "SM", "2025-07"],
        ]
        response_2026 = Mock()
        response_2026.raise_for_status = Mock()
        response_2026.json.return_value = [
            ["cell_value", "time_slot_id", "time_slot_date", "error_data", "category_code", "seasonally_adj", "data_type_code", "time"],
            ["735000", "1", "2026-07-01", "no", "44X72", "yes", "SM", "2026-07"],
        ]
        session = Mock()
        session.get.side_effect = [response_2025, response_2026]
        with patch.object(census, "request_with_retry", side_effect=lambda fn: fn()):
            result = census.fetch_census_retail_sales(
                date(2026, 7, 1), date(2026, 7, 31), api_key="secret", session=session
            )
        self.assertEqual(result, {date(2026, 7, 1): 5.0})
        self.assertEqual(session.get.call_count, 2)
        years = [call.kwargs["params"]["time"] for call in session.get.call_args_list]
        self.assertEqual(years, ["2025", "2026"])
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["params"]["category_code"], "44X72")
        self.assertEqual(kwargs["params"]["data_type_code"], "SM")
        self.assertEqual(kwargs["params"]["key"], "secret")
        self.assertNotIn("seasonally_adj", kwargs["params"])

    def test_chart_rows_store_yoy_in_existing_economic_chart_schema(self):
        rows = census.chart_rows({date(2026, 7, 1): 5.125})
        self.assertEqual(rows, [{
            "series_code": "US_RETAIL_SALES",
            "observation_date": "2026-07-01",
            "value": 5.125,
            "frequency": "M",
            "source": "CENSUS:MARTS/44X72/SM/SA/YOY",
        }])

    def test_automatic_workflow_and_frontend_include_census_retail_yoy_contract(self):
        automatic = (ROOT / ".github/workflows/economic-chart-data.yml").read_text(encoding="utf-8")
        chart = (ROOT / "assets/js/charts/economic-charts.js").read_text(encoding="utf-8")
        self.assertIn("CENSUS_API_KEY: ${{ secrets.CENSUS_API_KEY }}", automatic)
        self.assertIn("signals.economic_chart_automatic", automatic)
        self.assertFalse((ROOT / ".github/workflows/economic-chart-backfill-once.yml").exists())
        self.assertIn("code:'US_RETAIL_SALES'", chart)
        self.assertIn("title:'미국 소매판매 YoY'", chart)
        self.assertIn("unit:'% YoY'", chart)
        self.assertIn("frequency:'M'", chart)
        self.assertIn("M:[6,24,'6개월','24개월']", chart)


if __name__ == "__main__":
    unittest.main()
