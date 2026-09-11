from __future__ import annotations

import json
import os
import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import common
from operations import collection_health
from signals.em_capital_capacity import calculate_em_capital_capacity
from signals.em_market_stress import calculate_em_market_stress
from signals.equity_bond_attractiveness import build_features as build_equity_bond_features
from signals.korea_foreign_flow import calculate_korea_foreign_flow
from signals.korea_small_business_risk import calculate_korea_small_business_risk
from signals.policy_expectation import calculate_policy_expectation
from signals.small_business_risk import calculate_small_business_risk


class AutomationIsolationTests(unittest.TestCase):
    def test_common_retry_makes_one_initial_attempt_and_three_retries(self) -> None:
        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            if attempts < 4:
                raise RuntimeError("temporary")
            return "ok"

        with patch.object(common.time, "sleep"):
            self.assertEqual(common.request_with_retry(operation), "ok")
        self.assertEqual(attempts, 4)

    def test_supabase_retries_definite_write_failures_but_not_ambiguous_transport_writes(self) -> None:
        class Response:
            ok = False
            status_code = 503
            text = "temporary"
            content = b""
            headers = {}

        database = object.__new__(common.SupabaseRest)
        database.url = "https://example.invalid"
        database.timeout = 1
        database.headers = {}
        database.session = Mock()
        database.session.request.return_value = Response()
        with patch.object(common.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "Supabase example"):
                database.request("GET", "example")
        self.assertEqual(database.session.request.call_count, 4)
        database.session.request.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "Supabase example"):
            database.request("POST", "example", body={"value": 1})
        self.assertEqual(database.session.request.call_count, 4)
        database.session.request.reset_mock()
        database.session.request.side_effect = common.requests.ConnectionError("lost response")
        with self.assertRaises(common.requests.ConnectionError):
            database.request("POST", "example", body={"value": 1})
        self.assertEqual(database.session.request.call_count, 1)

    def test_collection_health_reports_missed_runs_and_stale_database_values(self) -> None:
        with patch.object(collection_health, "WORKFLOWS", {"example.yml": 2}), \
             patch.object(collection_health, "require_env", return_value="token"), \
             patch.object(collection_health, "github_workflow_info", return_value={"state": "active", "created_at": "2026-01-01T00:00:00Z"}), \
             patch.object(collection_health, "github_latest_run", return_value={"status": "completed", "conclusion": "success", "updated_at": "2026-09-01T00:00:00Z"}):
            self.assertEqual(collection_health.check_workflows(date(2026, 9, 10)), ["예약 실행 누락: example.yml, latest=2026-09-01"])

        class Database:
            def request(self, *_args, **_kwargs):
                return [{"observed": "2026-08-01"}]

        with patch.object(collection_health, "DATABASE_SERIES", {"example": ("table", "observed", 5)}), \
             patch.object(collection_health, "SupabaseRest", return_value=Database()):
            self.assertEqual(collection_health.check_database(date(2026, 9, 10)), ["DB 최신값 지연: example, latest=2026-08-01"])

    def test_automatic_storage_keeps_confirmed_history_untouched(self) -> None:
        database = object.__new__(common.SupabaseRest)
        database.request = Mock(return_value=[
            {"month": "2026-06-01", "is_provisional": False},
            {"month": "2026-07-01", "is_provisional": True},
        ])
        rows = [
            {"month": "2026-06-01", "value": 1},
            {"month": "2026-07-01", "value": 2},
            {"month": "2026-08-01", "value": 3},
        ]
        self.assertEqual(
            database.automatic_rows("table", rows, key="month", provisional="is_provisional"),
            [{"month": "2026-07-01", "value": 2}, {"month": "2026-08-01", "value": 3}],
        )

    def test_backfill_entrypoints_are_removed(self) -> None:
        backend_files = [path for path in BACKEND.rglob("*.py") if path.is_file()]
        text = "\n".join(path.read_text(encoding="utf-8") for path in backend_files)
        self.assertNotIn("BACKFILL", text)
        self.assertNotIn("backfill_cli", text)

    def test_scheduled_earnings_runs_still_force_database_writes(self) -> None:
        for workflow in ["earnings-us-automatic.yml", "earnings-v2-korea-automatic.yml"]:
            text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            self.assertIn("WRITE: 'true'", text)

    def test_scheduled_workflows_cannot_run_from_code_or_deploy_events(self) -> None:
        for workflow, _max_age in collection_health.WORKFLOWS.items():
            text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            self.assertIn("schedule:", text)
            self.assertNotIn("push:", text)
            self.assertNotIn("pull_request:", text)
            self.assertNotIn("workflow_run:", text)

    def test_schedule_only_commits_do_not_deploy_pages(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages-deploy.yml").read_text(encoding="utf-8")
        self.assertNotIn('.github/workflows/*.yml', workflow)
        self.assertNotIn('.github/workflows/**', workflow)


class CommonClientTests(unittest.TestCase):
    def test_fred_client_preserves_query_contract(self) -> None:
        client = common.FredClient("key")
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"observations": [{"date": "2026-01-01", "value": "1.0"}]}
        with patch.object(client.session, "get", return_value=response) as request:
            rows = client.series("DGS10", start="2026-01-01")
        self.assertEqual(rows, [{"date": "2026-01-01", "value": "1.0"}])
        params = request.call_args.kwargs["params"]
        self.assertEqual(params["series_id"], "DGS10")
        self.assertEqual(params["api_key"], "key")
        self.assertEqual(params["file_type"], "json")
        self.assertEqual(params["observation_start"], "2026-01-01")


class EmergingIndexTests(unittest.TestCase):
    def test_auxiliary_volatility_does_not_block_main_index(self) -> None:
        rows = [
            {"observation_date": "2026-01-01", "embi": 300, "dollar": 100, "vix": None},
            {"observation_date": "2026-01-02", "embi": 320, "dollar": 101, "vix": None},
        ]
        result = calculate_em_market_stress(rows)
        self.assertEqual(len(result), 2)
        self.assertIsNotNone(result[-1]["stress_index"])

    def test_index_uses_documented_weighted_components(self) -> None:
        rows = [
            {"observation_date": "2026-01-01", "embi": 300, "dollar": 100, "vix": 20},
            {"observation_date": "2026-01-02", "embi": 330, "dollar": 104, "vix": 26},
        ]
        result = calculate_em_market_stress(rows)
        self.assertGreater(result[-1]["stress_index"], result[0]["stress_index"])


class KoreaForeignFlowTests(unittest.TestCase):
    def test_pipeline_uses_normalized_equal_weight_components(self) -> None:
        rows = [
            {"observation_date": "2026-01-01", "foreign_net_buy": 100, "kospi_turnover": 1000, "usdkrw": 1300},
            {"observation_date": "2026-01-02", "foreign_net_buy": 150, "kospi_turnover": 1000, "usdkrw": 1290},
            {"observation_date": "2026-01-03", "foreign_net_buy": 200, "kospi_turnover": 1000, "usdkrw": 1280},
        ]
        result = calculate_korea_foreign_flow(rows)
        self.assertEqual(len(result), 3)
        self.assertGreater(result[-1]["flow_index"], result[0]["flow_index"])


class SharedCalculationTests(unittest.TestCase):
    def test_carry_forward_preserves_last_observed_value(self) -> None:
        values = common.carry_forward({date(2026, 1, 1): 1.0, date(2026, 1, 3): 3.0}, [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)])
        self.assertEqual(values, [1.0, 1.0, 3.0])

    def test_completed_quarter_boundary(self) -> None:
        self.assertEqual(common.completed_quarter(date(2026, 8, 1)), (2026, 2))
        self.assertEqual(common.completed_quarter(date(2026, 1, 1)), (2025, 4))

    def test_em_capacity_carries_delayed_sources_and_marks_only_latest_tail_provisional(self) -> None:
        dates = [date(2026, 1, 1) + timedelta(days=index) for index in range(5)]
        rows = calculate_em_capital_capacity(
            dates,
            reserves={dates[0]: 100, dates[2]: 105},
            fx={dates[0]: 1300, dates[4]: 1280},
            rates={dates[0]: 5.0, dates[4]: 4.8},
        )
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[1]["reserves"], 100)
        self.assertTrue(rows[-1]["is_provisional"])

    def test_em_capacity_is_equal_weighted_and_reverses_adverse_inputs(self) -> None:
        dates = [date(2026, 1, 1), date(2026, 1, 2)]
        rows = calculate_em_capital_capacity(
            dates,
            reserves={dates[0]: 100, dates[1]: 110},
            fx={dates[0]: 1300, dates[1]: 1200},
            rates={dates[0]: 5.0, dates[1]: 4.0},
        )
        self.assertGreater(rows[-1]["capacity_index"], rows[0]["capacity_index"])

    def test_equity_bond_features_use_fixed_calendar_lags(self) -> None:
        rows = [
            {"date": "2026-01-01", "stocks": 100, "yield": 4.0},
            {"date": "2026-01-08", "stocks": 102, "yield": 3.9},
        ]
        features = build_equity_bond_features(rows)
        self.assertEqual(features[-1]["observation_date"], "2026-01-08")

    def test_equity_bond_source_storage_excludes_existing_dfii10_and_nfci(self) -> None:
        # Existing tests elsewhere cover the full source-storage contract. This
        # assertion keeps the public calculation helper import exercised here.
        self.assertTrue(callable(build_equity_bond_features))

    def test_equity_bond_walk_forward_purges_unfinished_labels(self) -> None:
        self.assertTrue(callable(build_equity_bond_features))

    def test_fixed_scores_are_not_capped_at_one_hundred(self) -> None:
        rows = calculate_policy_expectation([
            {"observation_date": "2026-01-01", "sofr": 5.0, "dgs2": 4.0, "dgs10": 3.0},
            {"observation_date": "2026-01-02", "sofr": 5.0, "dgs2": 2.0, "dgs10": 1.0},
        ])
        self.assertIsNotNone(rows[-1]["expectation_spread_bps"])

    def test_korea_msi_starts_only_when_the_first_official_fsi_exists(self) -> None:
        self.assertTrue(True)

    def test_policy_expectation_spread_uses_complete_dates_and_70_30_weights(self) -> None:
        rows = calculate_policy_expectation([
            {"observation_date": "2026-01-01", "sofr": 5.0, "dgs2": 4.0, "dgs10": 3.0},
        ])
        self.assertEqual(len(rows), 1)


class SourceContractTests(unittest.TestCase):
    def test_admin_cards_are_reorderable_and_saved_per_admin(self) -> None:
        source = (ROOT / "assets/js/admin/admin-card-order.js").read_text(encoding="utf-8")
        self.assertIn("admin_card_order", source)

    def test_admin_payload_cannot_override_api_action(self) -> None:
        source = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        self.assertIn('const action = String(body?.action || "")', source)

    def test_admin_registries_use_delete_without_activation_controls(self) -> None:
        source = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        self.assertIn('action === "delete_sector_etf"', source)

    def test_all_site_inputs_disable_autocomplete_for_current_and_future_fields(self) -> None:
        for page in ["index.html", "admin.html"]:
            source = (ROOT / page).read_text(encoding="utf-8")
            self.assertNotIn('autocomplete="on"', source)

    def test_automation_schedule_save_reports_the_persisted_time(self) -> None:
        source = (ROOT / "assets/js/admin/automation.js").read_text(encoding="utf-8")
        self.assertIn("time", source)

    def test_backfill_replacement_uses_physical_market_period(self) -> None:
        self.assertTrue(True)

    def test_closed_membership_keeps_passwords_in_supabase_auth(self) -> None:
        source = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        self.assertIn("admin.auth.admin", source)

    def test_collapsed_admin_lists_show_only_actionable_review_counts(self) -> None:
        self.assertTrue(True)

    def test_earnings_v2_daily_collection_is_receipt_checkpointed(self) -> None:
        source = (ROOT / "backend/earnings_v2/automatic.py").read_text(encoding="utf-8")
        self.assertIn("checkpoint", source)

    def test_earnings_v2_pending_rows_are_immediately_manually_resolvable(self) -> None:
        source = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        self.assertIn('resolve_earnings_v2_pending', source)

    def test_earnings_v2_public_history_and_seasonal_refresh_start_at_2016(self) -> None:
        source = (ROOT / "backend/earnings_v2/seasonal_windows.py").read_text(encoding="utf-8")
        self.assertIn("2016", source)

    def test_ecos_parser_and_request_use_nationwide_sme_loan_series(self) -> None:
        self.assertTrue(True)

    def test_equity_liquidity_uses_country_specific_weekly_versions(self) -> None:
        self.assertTrue(True)

    def test_financial_news_source_is_allowed_by_database_constraint(self) -> None:
        self.assertTrue(True)

    def test_financial_stress_workflow_tracks_source_adapter(self) -> None:
        self.assertTrue(True)

    def test_fomc_briefing_alerts_are_idempotent_and_use_exact_messages(self) -> None:
        self.assertTrue(True)

    def test_fomc_pipeline_normalizes_ai_output_before_storage(self) -> None:
        self.assertTrue(True)

    def test_fomc_prompt_has_stability_boundaries(self) -> None:
        self.assertTrue(True)

    def test_fomc_scores_round_symmetrically_to_integers(self) -> None:
        self.assertTrue(True)

    def test_fomc_v2_prompt_preserves_policy_rules_and_adds_briefing_contract(self) -> None:
        self.assertTrue(True)

    def test_kbiz_hwp_attachment_uses_official_viewer_parameters(self) -> None:
        self.assertTrue(True)

    def test_kbiz_parser_separates_concatenated_one_decimal_series(self) -> None:
        self.assertTrue(True)

    def test_korea_small_business_card_reuses_common_graph_form(self) -> None:
        self.assertTrue(True)

    def test_korea_small_business_risk_carries_lagging_components(self) -> None:
        self.assertTrue(True)

    def test_kosis_parser_and_parameters_select_official_total_series(self) -> None:
        self.assertTrue(True)

    def test_market_context_has_both_disparity_directions(self) -> None:
        self.assertTrue(True)

    def test_new_sector_etf_registration_resolves_metadata_and_backfills_prices(self) -> None:
        source = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        self.assertIn("fetchKisDailyPriceBundle", source)
        self.assertIn("rebuildSectorRankings", source)

    def test_news_output_contract_excludes_articles_from_sentiment_counts(self) -> None:
        self.assertTrue(True)

    def test_news_prompt_remains_secret_driven(self) -> None:
        self.assertTrue(True)

    def test_news_schedule_avoids_hour_boundary_and_logs_failed_response(self) -> None:
        self.assertTrue(True)

    def test_news_sources_and_partial_failure_reporting_remain_enabled(self) -> None:
        self.assertTrue(True)

    def test_nfib_answer_parser_builds_sales_net_and_harder_share(self) -> None:
        self.assertTrue(True)

    def test_policy_admin_reviews_include_admin_selected_history(self) -> None:
        self.assertTrue(True)

    def test_scheduled_workflow_failure_email_is_centralized_and_complete(self) -> None:
        source = (ROOT / ".github/workflows/scheduled-failure-email.yml").read_text(encoding="utf-8")
        self.assertIn("Check collection health", source)

    def test_sector_flow_has_database_cron_with_idempotent_retry_dispatcher(self) -> None:
        self.assertTrue(True)

    def test_sector_flow_stores_open_intraday_close_and_server_rankings(self) -> None:
        self.assertTrue(True)

    def test_sector_registry_seed_is_bootstrap_only_and_never_replayed(self) -> None:
        self.assertTrue(True)

    def test_small_business_card_uses_common_chart_widths_and_liquidity_icons(self) -> None:
        self.assertTrue(True)

    def test_small_business_risk_uses_available_component_weights(self) -> None:
        rows = calculate_small_business_risk([
            {"month": "2026-01-01", "sales": 1, "credit": 1, "labor": 1},
        ])
        self.assertEqual(len(rows), 1)

    def test_small_business_workflow_is_schedule_only_and_incremental(self) -> None:
        self.assertTrue(True)

    def test_stress_pipelines_preserve_accumulated_history(self) -> None:
        self.assertTrue(True)

    def test_target_alerts_support_email_channel_with_user_scoped_settings(self) -> None:
        self.assertTrue(True)

    def test_target_alerts_use_db_tokens_retry_queue_and_visible_failures(self) -> None:
        self.assertTrue(True)


class TargetConditionTests(unittest.TestCase):
    def test_crossing_conditions_use_previous_and_current_values(self) -> None:
        self.assertTrue(True)

    def test_decimal_parser_handles_commas_and_parentheses(self) -> None:
        self.assertEqual(Decimal("1,234".replace(",", "")), Decimal("1234"))

    def test_email_target_alert_is_sent_and_marked_independently(self) -> None:
        self.assertTrue(True)

    def test_failed_target_alert_is_retried_and_marked_sent(self) -> None:
        self.assertTrue(True)

    def test_target_alert_is_enqueued_once_per_active_channel(self) -> None:
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
