from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources import us_treasury_yields as treasury  # noqa: E402


class TreasuryRealYieldChartTests(unittest.TestCase):
    def test_real_ten_year_rows_use_official_treasury_source(self) -> None:
        observed = date(2026, 9, 10)
        with patch.object(
            treasury,
            "fetch_treasury_real_values",
            return_value={"10Y": {observed: 1.82}},
        ):
            rows = treasury.fetch_treasury_real_yield_rows(observed, observed)["US10Y_REAL"]

        self.assertEqual(rows, [{
            "series_code": "US10Y_REAL",
            "observation_date": "2026-09-10",
            "value": 1.82,
            "frequency": "D",
            "source": "USTREASURY:daily_treasury_real_yield_curve",
        }])

    def test_automatic_and_frontend_include_real_ten_year(self) -> None:
        automatic = (ROOT / "backend/signals/economic_chart_automatic.py").read_text(encoding="utf-8")
        chart = (ROOT / "assets/js/charts/economic-charts.js").read_text(encoding="utf-8")

        self.assertIn('run("US10Y_REAL"', automatic)
        self.assertIn("{code:'US10Y_REAL',title:'미국채 10년 실질금리'", chart)
        self.assertFalse((ROOT / "backend/signals/economic_chart_backfill.py").exists())


if __name__ == "__main__":
    unittest.main()
