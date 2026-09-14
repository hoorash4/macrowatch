import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals.market_index_collection import INDEX_SYMBOLS, automatic, backfill, load_close


class FakeDatabase:
    def __init__(self, existing=None, pages=None):
        self.existing = existing or {}
        self.pages = list(pages or [])
        self.requests = []
        self.upserts = []

    def request(self, method, table, params=None):
        self.requests.append((method, table, params or {}))
        if method == "GET" and table == "market_index_prices":
            if self.pages:
                return self.pages.pop(0)
            code = (params or {}).get("index_code", "").replace("eq.", "")
            return [{"market_date": day} for day in self.existing.get(code, set())]
        return []

    def upsert(self, table, rows, *, conflict):
        self.upserts.append((table, list(rows), conflict))


def row(code, day, close=100.0):
    return {
        "index_code": code,
        "market_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
        "source": "TEST",
        "updated_at": "2026-09-14T00:00:00+00:00",
    }


class MarketIndexCollectionTests(unittest.TestCase):
    def test_canonical_index_set(self):
        self.assertEqual({
            "SP500": "^GSPC",
            "NASDAQ_COMPOSITE": "^IXIC",
            "KOSPI": "^KS11",
        }, INDEX_SYMBOLS)

    def test_backfill_fetches_all_before_deleting_and_replaces_range(self):
        db = FakeDatabase()
        start, end = date(1990, 1, 1), date(1990, 1, 8)
        samples = {
            code: [row(code, "1990-01-02"), row(code, "1990-01-08", 101.0)]
            for code in INDEX_SYMBOLS
        }
        with patch("signals.market_index_collection.fetch_index_candles",
                   side_effect=lambda code, _start, _end: samples[code]):
            stored = backfill(db, start=start, end=end)
        self.assertEqual({code: 2 for code in INDEX_SYMBOLS}, stored)
        deletes = [call for call in db.requests if call[0] == "DELETE"]
        self.assertEqual(3, len(deletes))
        self.assertTrue(all("market_date.gte.1990-01-01" in call[2]["and"] for call in deletes))
        self.assertEqual(3, len(db.upserts))

    def test_backfill_does_not_delete_if_any_source_fails_validation(self):
        db = FakeDatabase()
        start, end = date(1990, 1, 1), date(1990, 1, 8)
        def sample(code, _start, _end):
            if code == "NASDAQ_COMPOSITE":
                return [row(code, "1991-01-02")]
            return [row(code, "1990-01-02"), row(code, "1990-01-08")]
        with patch("signals.market_index_collection.fetch_index_candles", side_effect=sample):
            with self.assertRaisesRegex(RuntimeError, "coverage validation"):
                backfill(db, start=start, end=end)
        self.assertFalse(any(call[0] == "DELETE" for call in db.requests))
        self.assertEqual([], db.upserts)

    def test_automatic_only_writes_missing_or_current_rows(self):
        today = date(2026, 9, 14)
        db = FakeDatabase(existing={
            code: {"2026-09-11", "2026-09-14"} for code in INDEX_SYMBOLS
        })
        def sample(code, _start, _end):
            return [row(code, "2026-09-11"), row(code, "2026-09-12"), row(code, "2026-09-14")]
        with patch("signals.market_index_collection.fetch_index_candles", side_effect=sample):
            stored = automatic(db, today=today)
        self.assertEqual({code: 2 for code in INDEX_SYMBOLS}, stored)
        for _, rows, _ in db.upserts:
            self.assertEqual(["2026-09-12", "2026-09-14"], [r["market_date"] for r in rows])

    def test_load_close_reads_market_index_prices(self):
        db = FakeDatabase(pages=[[{"market_date": "2026-09-11", "close": "123.45"}]])
        values = load_close(db, "SP500", date(2026, 9, 1), date(2026, 9, 14))
        self.assertEqual({date(2026, 9, 11): 123.45}, values)
        self.assertEqual("eq.SP500", db.requests[0][2]["index_code"])

    def test_stress_pipelines_do_not_collect_duplicate_raw_indices(self):
        financial = (ROOT / "backend/signals/financial_stress_pipeline.py").read_text(encoding="utf-8")
        korea = (ROOT / "backend/signals/korea_stress_pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn('"SP500": (_valid_fred_values', financial)
        self.assertIn('load_market_index_close(canonical, "SP500"', financial)
        self.assertNotIn('"kospi_close": ("KOSPI_CLOSE"', korea)
        self.assertIn('load_market_index_close(database, "KOSPI"', korea)


if __name__ == "__main__":
    unittest.main()
