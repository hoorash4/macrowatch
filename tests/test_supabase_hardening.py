from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260913235500_harden_rls_and_foreign_key_indexes.sql"


class SupabaseHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_anonymous_target_crud_is_removed(self) -> None:
        for operation in ("select", "insert", "update", "delete"):
            self.assertIn(f'drop policy if exists "allow anon {operation} targets"', self.sql)
        self.assertIn("revoke all on table public.targets from anon", self.sql)

    def test_internal_definer_functions_are_service_only(self) -> None:
        for signature in (
            "public.earnings_v2_us_active_companies(integer)",
            "public.earnings_v2_us_get_universe(text, integer, smallint)",
            "public.earnings_v2_us_market_facts(text, integer, smallint)",
            "public.rls_auto_enable()",
        ):
            self.assertIn(f"revoke all on function {signature} from public, anon, authenticated", self.sql)
            self.assertIn(f"grant execute on function {signature} to service_role", self.sql)

    def test_rls_uid_checks_use_statement_initplan(self) -> None:
        self.assertNotIn("auth.uid() = user_id", self.sql)
        self.assertIn("(select auth.uid()) = user_id", self.sql)

    def test_missing_foreign_key_indexes_are_added(self) -> None:
        for name in (
            "app_settings_updated_by_idx",
            "economic_chart_catalog_settings_updated_by_idx",
            "market_sector_weekly_rankings_etf_id_idx",
            "news_event_feedback_reviewed_by_idx",
        ):
            self.assertIn(f"create index if not exists {name}", self.sql)


if __name__ == "__main__":
    unittest.main()
