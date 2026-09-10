from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class CollectorIsolationTests(unittest.TestCase):
    def test_pages_deploy_is_frontend_only(self) -> None:
        workflow = text(".github/workflows/pages-deploy.yml")
        self.assertIn('paths:', workflow)
        self.assertNotIn('paths-ignore:', workflow)
        self.assertNotIn('- "backend/**"', workflow)
        self.assertNotIn('- "supabase/**"', workflow)

    def test_supabase_deploy_does_not_replay_history_or_redeploy_everything(self) -> None:
        workflow = text(".github/workflows/deploy-supabase.yml")
        self.assertIn("--diff-filter=A", workflow)
        self.assertIn("Existing migrations must not be modified or deleted", workflow)
        self.assertNotIn("20260826_add_policy_previous_peak_scoring.sql", workflow)
        self.assertNotIn('for fn in supabase/functions/*', workflow)
        self.assertNotIn('functions delete', workflow)

    def test_policy_expectation_automatic_storage_is_missing_only(self) -> None:
        pipeline = text("backend/signals/policy_expectation_pipeline.py")
        self.assertIn("database.automatic_rows(", pipeline)
        self.assertIn('key="observation_date"', pipeline)

    def test_korea_foreign_flow_does_not_rewrite_or_prune_history(self) -> None:
        function = text("supabase/functions/korea-foreign-flow/index.ts")
        self.assertIn("const publishable = calculated.filter", function)
        self.assertIn('.from("korea_foreign_flow_daily").insert(publishable)', function)
        self.assertNotIn('.upsert(calculated.filter', function)
        self.assertNotIn('.from("korea_foreign_flow_daily").delete()', function)
        self.assertNotIn('.from("korea_foreign_flow_raw").delete()', function)

    def test_sector_collection_does_not_run_retention_deletes(self) -> None:
        function = text("supabase/functions/sector-flow/index.ts")
        self.assertNotIn('.delete().lt("market_date", retentionStart)', function)
        self.assertNotIn('.delete().lt("week_start", rankingRetentionStart)', function)
        # Replacing the current week's calculated rows is part of the current
        # collection transaction, not historical retention cleanup.
        self.assertIn('.eq("week_start", currentWeek)', function)

    def test_inflation_policy_tail_is_not_broadly_rewritten(self) -> None:
        pipeline = text("backend/inflation_pipeline.py")
        self.assertNotIn('client.upsert("us_policy_rate_daily", rows[-10:]', pipeline)

    def test_manual_earnings_recalculation_is_provider_free_and_separate(self):
        service = (ROOT / "backend/earnings_v2/recalculation.py").read_text(encoding="utf-8")
        cli = (ROOT / "backend/earnings_v2/recalculate_cli.py").read_text(encoding="utf-8")
        automatic = (ROOT / "backend/earnings_v2/automatic.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/earnings-v2-korea.yml").read_text(encoding="utf-8")
        self.assertNotIn(".providers import", service)
        self.assertNotIn("automatic import", service)
        self.assertIn("StoredQuarterRecalculation.from_env()", cli)
        self.assertNotIn("KoreaEarningsV2AutomaticPipeline", cli)
        self.assertNotIn("def recalculate_quarter(", automatic)
        self.assertIn("StoredQuarterRecalculation(self.repository).recalculate_quarter", automatic)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("earnings_v2.recalculate_cli", workflow)


if __name__ == "__main__":
    unittest.main()
