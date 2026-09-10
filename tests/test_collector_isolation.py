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

    def test_market_context_automatic_is_missing_only_and_bootstrap_is_explicit(self) -> None:
        function = text("supabase/functions/market-context/index.ts")
        workflow = text(".github/workflows/market-context.yml")
        self.assertIn('type CollectionMode = "automatic" | "bootstrap"', function)
        self.assertIn('body.mode === "bootstrap" ? "bootstrap" : "automatic"', function)
        self.assertIn('const publishable = rows.filter', function)
        self.assertIn('.from("market_index_prices").insert(publishable)', function)
        self.assertNotIn('(count || 0) >= 80 ? REFRESH_DAYS : BOOTSTRAP_DAYS', function)
        self.assertIn("github.event_name == 'schedule' && 'automatic'", workflow)
        self.assertIn('options: [automatic, bootstrap]', workflow)

    def test_sector_collection_does_not_run_retention_deletes_or_initialize_history(self) -> None:
        function = text("supabase/functions/sector-flow/index.ts")
        workflow = text(".github/workflows/sector-flow.yml")
        self.assertNotIn('.delete().lt("market_date", retentionStart)', function)
        self.assertNotIn('.delete().lt("week_start", rankingRetentionStart)', function)
        self.assertIn('const initializeHistory = body.initialize_history === true', function)
        self.assertIn('const needsHistoryInitialization = initializeHistory && missingHistoryIds.has(item.id)', function)
        self.assertIn('const benchmarkStart = initializeHistory ? new Date(`${retentionStart}T00:00:00Z`) : end', function)
        self.assertIn("github.event_name == 'schedule' && 'collect'", workflow)
        self.assertIn('options: [collect, initialize_history, rebuild_rankings]', workflow)
        # Replacing the current week's calculated rows is part of the current
        # collection transaction, not historical retention cleanup.
        self.assertIn('.eq("week_start", currentWeek)', function)

    def test_policy_score_recalculation_skips_unchanged_history_writes(self) -> None:
        store = text("supabase/functions/_shared/policy/policy-score-store.ts")
        self.assertIn("const SCORE_FIELDS", store)
        self.assertIn("const unchanged = stored && SCORE_FIELDS.every", store)
        self.assertIn("if (unchanged) continue", store)

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
        self.assertIn("self.recalculation.recalculate_quarter", automatic)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("earnings_v2.recalculate_cli", workflow)


if __name__ == "__main__":
    unittest.main()
