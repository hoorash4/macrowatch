from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.us_treasury_yields import parse_treasury_yield_xml  # noqa: E402
from sources.yahoo_daily import parse_yahoo_daily_payload  # noqa: E402


class EconomicChartLiveSourceTests(unittest.TestCase):
    def test_automatic_daily_market_sources_do_not_use_fred(self):
        automatic = (ROOT / "backend/signals/economic_chart_automatic.py").read_text(encoding="utf-8")
        backfill = (ROOT / "backend/signals/economic_chart_backfill.py").read_text(encoding="utf-8")

        self.assertIn('LIVE_NON_FRED_SERIES = {"US2Y", "US10Y", "US10Y2Y", "WTI", "USDKRW"}', automatic)
        self.assertIn('fetch_treasury_yield_rows(start, today)', automatic)
        self.assertIn('fetch_yahoo_daily_rows("USDKRW", "KRW=X", start, today)', automatic)
        self.assertIn('_derive_spread(db, "US10Y2Y", "US10Y", "US2Y", "D", start, today)', automatic)
        self.assertIn('if code in LIVE_NON_FRED_SERIES:', automatic)
        # Historical backfill remains provider-stable and explicit.
        self.assertIn('for code, (source_id, frequency) in FRED_SERIES.items()', backfill)

    def test_treasury_xml_parses_official_two_and_ten_year_rates(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
              xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
          <entry><content><m:properties>
            <d:NEW_DATE>2026-09-10T00:00:00</d:NEW_DATE>
            <d:BC_2YEAR>4.11</d:BC_2YEAR>
            <d:BC_10YEAR>4.84</d:BC_10YEAR>
          </m:properties></content></entry>
        </feed>"""
        rows = parse_treasury_yield_xml(xml, date(2026, 9, 1), date(2026, 9, 11))
        self.assertEqual(rows["US2Y"][0]["value"], 4.11)
        self.assertEqual(rows["US10Y"][0]["value"], 4.84)
        self.assertEqual(rows["US10Y"][0]["source"], "USTREASURY:daily_treasury_yield_curve")

    def test_yahoo_daily_parser_keeps_market_close_and_provenance(self):
        stamp = int(datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc).timestamp())
        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "timestamp": [stamp],
                    "indicators": {"quote": [{"close": [1392.25]}]},
                }],
            }
        }
        rows = parse_yahoo_daily_payload(
            payload,
            series_code="USDKRW",
            symbol="KRW=X",
            start=date(2026, 9, 1),
            end=date(2026, 9, 11),
        )
        self.assertEqual(rows, [{
            "series_code": "USDKRW",
            "observation_date": "2026-09-10",
            "value": 1392.25,
            "frequency": "D",
            "source": "YAHOO:KRW=X",
        }])


if __name__ == "__main__":
    unittest.main()
