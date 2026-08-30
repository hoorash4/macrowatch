from datetime import date
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.universe import (  # noqa: E402
    CompanyState,
    Constituent,
    MembershipState,
    backfill_periods,
    plan_universe_sync,
)
from earnings.collection_coverage import (  # noqa: E402
    CollectionCoverageError,
    validate_collection_universe,
)


class EarningsUniverseTests(unittest.TestCase):
    def test_sec_function_migration_is_safe_after_return_shape_extension(self):
        migration = (ROOT / "supabase/migrations/20260828_add_sec_earnings_functions.sql").read_text(
            encoding="utf-8"
        )
        drop_at = migration.index(
            "drop function if exists public.list_current_sec_earnings_companies();"
        )
        create_at = migration.index(
            "create function public.list_current_sec_earnings_companies()"
        )
        self.assertLess(drop_at, create_at)

    def test_collection_union_is_dynamic_and_deduplicates_cross_index_companies(self):
        coverage = {
            "indices": [
                {"index_id": "NASDAQ100", "target_count": 100, "active_membership_count": 100},
                {"index_id": "SP100", "target_count": 100, "active_membership_count": 100},
            ],
            "unique_companies": 3,
            "missing_identifier_tickers": [],
        }
        companies = [
            {"company_id": "one", "cik": "1"},
            {"company_id": "two", "cik": "2"},
            {"company_id": "two", "cik": "old-2"},
            {"company_id": "three", "cik": "3"},
        ]
        self.assertEqual(
            validate_collection_universe(coverage, companies, company_id_key="company_id"),
            3,
        )

    def test_collection_union_fails_closed_on_membership_or_identifier_gap(self):
        coverage = {
            "indices": [
                {"index_id": "KOSPI100", "target_count": 100, "active_membership_count": 99},
            ],
            "unique_companies": 99,
            "missing_identifier_tickers": [],
        }
        with self.assertRaisesRegex(CollectionCoverageError, "99/100"):
            validate_collection_universe(coverage, [], company_id_key="id")

        coverage["indices"][0]["active_membership_count"] = 100
        coverage["unique_companies"] = 100
        coverage["missing_identifier_tickers"] = ["005930"]
        with self.assertRaisesRegex(CollectionCoverageError, "005930"):
            validate_collection_universe(coverage, [], company_id_key="id")

    def test_snapshot_classifies_new_reentry_cross_index_and_exit(self):
        companies = {
            "000001": CompanyState("company-reentry", "000001", True, False, (2019, "11011")),
            "000002": CompanyState("company-cross", "000002", True, True, (2026, "11012")),
            "000004": CompanyState("company-exit", "000004", True, True, (2026, "11012")),
        }
        current = [MembershipState("company-exit", "000004", date(2025, 6, 1))]
        plan = plan_universe_sync(
            [
                Constituent("000001", "재진입"),
                Constituent("000002", "다른 지수 편입 중"),
                Constituent("000003", "완전 신규"),
            ],
            companies_by_ticker=companies,
            current_memberships=current,
            effective_from=date(2026, 9, 1),
            expected_count=3,
        )
        self.assertEqual(
            [(item.constituent.ticker, item.kind) for item in plan.additions],
            [
                ("000001", "reentry"),
                ("000002", "cross_index_addition"),
                ("000003", "new_company"),
            ],
        )
        self.assertEqual(plan.exits[0].ticker, "000004")
        self.assertEqual(plan.exits[0].effective_to, date(2026, 8, 31))

    def test_new_company_gets_ten_years_and_cross_index_gets_no_backfill(self):
        periods = backfill_periods(as_of_year=2026, kind="new_company")
        self.assertEqual(len(periods), 40)
        self.assertEqual(periods[0], (2017, "11013"))
        self.assertEqual(periods[-1], (2026, "11011"))
        self.assertEqual(backfill_periods(as_of_year=2026, kind="cross_index_addition"), [])

    def test_reentry_fills_the_entire_gap_even_when_longer_than_ten_years(self):
        periods = backfill_periods(
            as_of_year=2026,
            kind="reentry",
            last_complete_period=(2019, "11011"),
        )
        self.assertEqual(len(periods), 28)
        self.assertEqual(periods[0], (2020, "11013"))
        self.assertEqual(periods[-1], (2026, "11011"))

    def test_snapshot_count_and_duplicate_tickers_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "does not match expected"):
            plan_universe_sync(
                [Constituent("000001", "한곳")],
                companies_by_ticker={},
                current_memberships=[],
                effective_from=date(2026, 9, 1),
                expected_count=2,
            )
        with self.assertRaisesRegex(ValueError, "Duplicate constituent"):
            plan_universe_sync(
                [Constituent("000001", "한곳"), Constituent("000001", "중복")],
                companies_by_ticker={},
                current_memberships=[],
                effective_from=date(2026, 9, 1),
            )


if __name__ == "__main__":
    unittest.main()
