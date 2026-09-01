from dataclasses import asdict
from datetime import date
from decimal import Decimal
import unittest

from earnings_v2.dart_financials import DartBatchResult, DartQuarterFinancials
from earnings_v2.korea_pipeline import KoreaEarningsPipeline
from earnings_v2.krx import KrxSecurity
from earnings_v2.kis_financials import KisTopLineResult


class FakeKrx:
    @staticmethod
    def last_trading_day(_market_id, trading_date):
        return trading_date, [
            KrxSecurity(
                stock_code=f"{index:06d}",
                name=f"기업{index}",
                close=Decimal("100"),
                market_cap=Decimal(1000 - index),
                listed_shares=Decimal("10"),
                reference_date=trading_date,
            )
            for index in range(1, 101)
        ]


class FakeKrxWithEarlierMarketClose(FakeKrx):
    @staticmethod
    def last_trading_day(_market_id, trading_date):
        reference_date, rows = FakeKrx.last_trading_day(_market_id, trading_date)
        earlier = date(reference_date.year, 12, 30)
        return earlier, [
            KrxSecurity(
                stock_code=row.stock_code,
                name=row.name,
                close=row.close,
                market_cap=row.market_cap,
                listed_shares=row.listed_shares,
                reference_date=earlier,
            )
            for row in rows
        ]


class FakeDart:
    @staticmethod
    def corp_code_map():
        return {
            f"{index:06d}": (f"{index:08d}", f"기업{index}")
            for index in range(1, 101)
        }


class FakeFx:
    def __init__(self, rate="1300"):
        self.rate = Decimal(rate)
        self.calls = []

    def usd_krw_on_or_before(self, reference_date):
        self.calls.append(reference_date)
        return self.rate


class FakeFinancialCollector:
    def __init__(self, usd_codes=()):
        self.calls = []
        self.usd_codes = set(usd_codes)

    def collect(self, corp_codes, year, quarter):
        codes = list(corp_codes)
        self.calls.append((codes, year, quarter))
        return DartBatchResult(
            values={
                code: DartQuarterFinancials(
                    top_line=Decimal("100"),
                    operating_income=Decimal("20"),
                    net_income=Decimal("10"),
                    scope="CFS",
                    source_filing_id="20260515000001",
                    currency="USD" if code in self.usd_codes else "KRW",
                )
                for code in codes
            },
            errors={},
        )


class FakeKisTopLines:
    def __init__(self, values=None, errors=None):
        self.values = values or {}
        self.errors = errors or {}
        self.calls = []

    def collect(self, tickers, year, quarter):
        requested = list(tickers)
        self.calls.append((requested, year, quarter))
        return KisTopLineResult(
            values={ticker: self.values[ticker] for ticker in requested if ticker in self.values},
            errors={ticker: self.errors[ticker] for ticker in requested if ticker in self.errors},
            request_counts={"edge_calls": 1, "tickers": len(requested)},
        )


class FakeStore:
    def __init__(self):
        self.company_quarters = {}
        self.market_quarters = {}
        self.states = []

    @staticmethod
    def upsert_companies(_rows):
        return 0

    @staticmethod
    def upsert_identifiers(_rows):
        return 0

    @staticmethod
    def replace_universe(_market_id, _year, _quarter, rows):
        return len(list(rows))

    def get_company_quarters_many(self, company_ids):
        return [
            asdict(row)
            for company_id in company_ids
            for row in self.company_quarters.get(company_id, [])
        ]

    def upsert_company_quarters(self, rows):
        for row in rows:
            history = self.company_quarters.setdefault(row.company_id, [])
            history[:] = [existing for existing in history if existing.key != row.key] + [row]
        return len(list(rows))

    def get_market_quarters(self, market_id):
        return [asdict(row) for row in self.market_quarters.get(market_id, [])]

    def upsert_market_quarters(self, rows):
        for row in rows:
            history = self.market_quarters.setdefault(row.market_id, [])
            history[:] = [existing for existing in history if existing.key != row.key] + [row]
        return len(list(rows))

    def save_pipeline_state(self, **state):
        self.states.append(state)


class EarningsV2KoreaPipelineTests(unittest.TestCase):
    def test_full_flow_persists_one_hundred_and_rerun_makes_no_financial_call(self):
        store = FakeStore()
        pipeline = KoreaEarningsPipeline(
            krx=FakeKrx(), dart=FakeDart(), fx=FakeFx(), store=store,
        )
        financials = FakeFinancialCollector()
        pipeline.financials = financials

        first = pipeline.run(
            [("kr_largecap", 2026, 1)],
            source="test",
            operation="one_quarter",
        )
        self.assertEqual(first["status"], "ready")
        self.assertEqual(len(financials.calls), 1)
        self.assertEqual(len(financials.calls[0][0]), 100)
        self.assertEqual(sum(len(rows) for rows in store.company_quarters.values()), 100)
        company = store.company_quarters["kr:00000001"][0]
        self.assertEqual(company.operating_margin_pct, Decimal("20.00000000"))
        self.assertEqual(company.net_margin_pct, Decimal("10.00000000"))
        market = store.market_quarters["kr_largecap"][0]
        self.assertEqual(market.actual_company_count, 100)
        self.assertEqual(market.operating_margin_pct, Decimal("20.00000000"))
        self.assertEqual(market.net_margin_pct, Decimal("10.00000000"))

        second = pipeline.run(
            [("kr_largecap", 2026, 1)],
            source="test",
            operation="one_quarter",
        )
        self.assertEqual(second["status"], "ready")
        self.assertEqual(len(financials.calls), 1)

    def test_usd_company_is_kept_and_saved_as_quarter_end_krw(self):
        store = FakeStore()
        fx = FakeFx("1300")
        pipeline = KoreaEarningsPipeline(
            krx=FakeKrx(), dart=FakeDart(), fx=fx, store=store,
        )
        pipeline.financials = FakeFinancialCollector(usd_codes={"00000001"})

        result = pipeline.run(
            [("kr_largecap", 2026, 1)],
            source="test",
            operation="usd_conversion",
        )

        self.assertEqual(result["status"], "ready")
        converted = store.company_quarters["kr:00000001"][0]
        self.assertEqual(converted.currency, "KRW")
        self.assertEqual(converted.top_line, Decimal("130000"))
        self.assertEqual(converted.operating_income, Decimal("26000"))
        self.assertEqual(converted.net_income, Decimal("13000"))
        self.assertEqual(converted.operating_margin_pct, Decimal("20.00000000"))
        self.assertEqual(converted.net_margin_pct, Decimal("10.00000000"))
        self.assertEqual(fx.calls, [date(2026, 3, 31)])

    def test_fx_uses_calendar_quarter_end_not_earlier_market_close(self):
        store = FakeStore()
        fx = FakeFx("1300")
        pipeline = KoreaEarningsPipeline(
            krx=FakeKrxWithEarlierMarketClose(), dart=FakeDart(), fx=fx, store=store,
        )
        pipeline.financials = FakeFinancialCollector(usd_codes={"00000001"})

        result = pipeline.run(
            [("kr_largecap", 2026, 4)],
            source="test",
            operation="quarter_end_fx",
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(fx.calls, [date(2026, 12, 31)])

    def test_partial_financials_are_persisted_for_later_completion(self):
        class PartialCollector(FakeFinancialCollector):
            def collect(self, corp_codes, year, quarter):
                result = super().collect(corp_codes, year, quarter)
                result.values["00000001"] = DartQuarterFinancials(
                    top_line=None,
                    operating_income=Decimal("20"),
                    net_income=Decimal("10"),
                    scope="CFS",
                    source_filing_id="20260515000001",
                    currency="KRW",
                )
                result.errors["00000001"] = "CFS(top_line=no,op=yes,net=yes)"
                return result

        store = FakeStore()
        pipeline = KoreaEarningsPipeline(
            krx=FakeKrx(), dart=FakeDart(), fx=FakeFx(), store=store,
        )
        pipeline.financials = PartialCollector()

        result = pipeline.run(
            [("kr_largecap", 2026, 1)],
            source="test",
            operation="partial_persistence",
        )

        self.assertEqual(result["status"], "incomplete")
        saved = store.company_quarters["kr:00000001"][0]
        self.assertIsNone(saved.top_line)
        self.assertEqual(saved.operating_income, Decimal("20"))
        self.assertEqual(saved.net_income, Decimal("10"))
        self.assertIsNone(saved.operating_margin_pct)
        self.assertIsNone(saved.net_margin_pct)
        self.assertEqual(saved.quality_status, "review_required")

    def test_kis_supplements_only_the_missing_top_line(self):
        class PartialCollector(FakeFinancialCollector):
            def collect(self, corp_codes, year, quarter):
                result = super().collect(corp_codes, year, quarter)
                result.values["00000001"] = DartQuarterFinancials(
                    top_line=None,
                    operating_income=Decimal("20"),
                    net_income=Decimal("10"),
                    scope="CFS",
                    source_filing_id="20260515000001",
                    currency="KRW",
                )
                result.errors["00000001"] = "CFS(top_line=no,op=yes,net=yes)"
                return result

        store = FakeStore()
        kis = FakeKisTopLines(values={"000001": Decimal("200")})
        pipeline = KoreaEarningsPipeline(
            krx=FakeKrx(), dart=FakeDart(), fx=FakeFx(), store=store,
            kis_top_lines=kis,
        )
        pipeline.financials = PartialCollector()

        result = pipeline.run(
            [("kr_largecap", 2026, 1)],
            source="test",
            operation="kis_top_line",
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(kis.calls, [(["000001"], 2026, 1)])
        saved = store.company_quarters["kr:00000001"][0]
        self.assertEqual(saved.top_line, Decimal("200"))
        self.assertEqual(saved.operating_income, Decimal("20"))
        self.assertEqual(saved.net_income, Decimal("10"))
        self.assertEqual(saved.operating_margin_pct, Decimal("10.00000000"))
        self.assertEqual(saved.net_margin_pct, Decimal("5.00000000"))


if __name__ == "__main__":
    unittest.main()
