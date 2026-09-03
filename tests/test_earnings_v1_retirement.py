from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EarningsV1RetirementTests(unittest.TestCase):
    def test_v1_runtime_paths_are_removed(self):
        self.assertFalse((ROOT / "backend/earnings").exists())
        for workflow in (
            "diagnose-legacy-dart.yml",
            "earnings-company-price-gaps.yml",
            "earnings-growth-metrics.yml",
            "earnings-index-prices.yml",
            "earnings-open-dart-legacy.yml",
            "earnings-open-dart.yml",
            "earnings-sec.yml",
            "earnings-universe.yml",
        ):
            self.assertFalse((ROOT / ".github/workflows" / workflow).exists())

    def test_cleanup_targets_v1_and_explicitly_excludes_v2(self):
        sql = (
            ROOT / "supabase/migrations/20260904000000_remove_earnings_v1.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("p.proname not ilike 'earnings_v2_%'", sql)
        self.assertNotIn("pg_get_functiondef", sql)
        self.assertIn("drop table if exists public.earnings_companies", sql)

    def test_active_deploy_does_not_replay_v1(self):
        workflow = (ROOT / ".github/workflows/deploy-supabase.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("20260828_add_earnings_foundation.sql", workflow)
        self.assertIn("20260904000000_remove_earnings_v1.sql", workflow)


if __name__ == "__main__":
    unittest.main()
