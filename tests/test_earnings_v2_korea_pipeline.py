from dataclasses import asdict
from datetime import date
from decimal import Decimal
import unittest

from earnings_v2.dart_financials import DartBatchResult, DartQuarterFinancials
from earnings_v2.korea_pipeline import KoreaEarningsPipeline
from earnings_v2.krx import KrxSecurity


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


class FakeDart:
    @staticmethod
    def corp_code_map():
        return {
            f"{index:06d}": (f"{index:08d}", f"기업{index}")
            for index in range(1, 101)
        }


class FakeFinancialCollector:
    def __init__(self):
        self.calls = []

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
                    top_line_method="reported_total",
                    source_filing_id="20260515000001",
                    currency="KRW",
                )
                for code in codes
            },
            errors={},
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
        pipeline = KoreaEarningsPipeline(krx=FakeKrx(), dart=FakeDart(), store=store)
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
        self.assertEqual(store.market_quarters["kr_largecap"][0].actual_company_count, 100)

        second = pipeline.run(
            [("kr_largecap", 2026, 1)],
            source="test",
            operation="one_quarter",
        )
        self.assertEqual(second["status"], "ready")
        self.assertEqual(len(financials.calls), 1)


if __name__ == "__main__":
    unittest.main()

