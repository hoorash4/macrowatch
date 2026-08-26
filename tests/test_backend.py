from __future__ import annotations

import sys
import types
import unittest
import re
from datetime import date
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# 계산 테스트에는 네트워크가 필요 없다. 로컬에 운영 의존성이 설치되지 않은
# 환경에서도 모듈을 불러올 수 있도록 import 자리만 제공한다.
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = object
    sys.modules["requests"] = requests_stub
if "openpyxl" not in sys.modules:
    sys.modules["openpyxl"] = types.ModuleType("openpyxl")

import check_targets  # noqa: E402
import common  # noqa: E402
import em_stress_pipeline as em  # noqa: E402
import financial_stress_pipeline as us  # noqa: E402
import korea_stress_pipeline as kr  # noqa: E402


class TargetConditionTests(unittest.TestCase):
    def test_decimal_parser_handles_commas_and_parentheses(self) -> None:
        self.assertEqual(check_targets.parse_decimal("1,234.50"), Decimal("1234.50"))
        self.assertEqual(check_targets.parse_decimal("(12.5)"), Decimal("-12.5"))

    def test_crossing_conditions_use_previous_and_current_values(self) -> None:
        target = {"condition_type": "gte", "target_value": "10"}
        self.assertTrue(check_targets.condition_met(target, Decimal("9"), Decimal("11")))
        self.assertFalse(check_targets.condition_met(target, Decimal("11"), Decimal("12")))


class SharedCalculationTests(unittest.TestCase):
    def test_carry_forward_preserves_last_observed_value(self) -> None:
        periods = ["2026-01-02", "2026-01-09", "2026-01-16"]
        expected = {"2026-01-02": 1.0, "2026-01-09": 1.0, "2026-01-16": 3.0}
        self.assertEqual(us.carry_forward_values({periods[0]: 1.0, periods[2]: 3.0}, periods), expected)
        self.assertEqual(em.carry_forward({periods[0]: 1.0, periods[2]: 3.0}, periods), expected)

    def test_fixed_scores_are_not_capped_at_one_hundred(self) -> None:
        self.assertEqual(us.fixed_stress_score(38.0, "high_yield_oas_pct"), 200.0)
        self.assertEqual(em.score(30.0, 10.0, 20.0), 200.0)
        self.assertEqual(kr.score(30.0, 10.0, 20.0), 200.0)

    def test_completed_quarter_boundary(self) -> None:
        self.assertEqual(us.latest_completed_quarter_end(date(2026, 8, 26)), date(2026, 6, 30))
        self.assertEqual(us.latest_completed_quarter_end(date(2026, 1, 2)), date(2025, 12, 31))


class CommonClientTests(unittest.TestCase):
    def test_fred_client_preserves_query_contract(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"observations": [{"date": "2026-08-01", "value": "1.5"}]}

        class Session:
            def __init__(self) -> None:
                self.call = None

            def get(self, url, **kwargs):
                self.call = (url, kwargs)
                return Response()

        session = Session()
        rows = common.fetch_fred_observations(
            "SERIES",
            "KEY",
            start="2026-08-01",
            end="2026-08-31",
            timeout=12,
            session=session,
        )
        self.assertEqual(rows[0]["value"], "1.5")
        self.assertEqual(session.call[1]["params"]["series_id"], "SERIES")
        self.assertEqual(session.call[1]["params"]["observation_start"], "2026-08-01")
        self.assertEqual(session.call[1]["timeout"], 12)


class SourceContractTests(unittest.TestCase):
    def test_sector_registry_seeds_verified_domestic_etfs_once_per_sector(self):
        migration = (ROOT / "supabase/migrations/20260827_seed_domestic_sector_etfs.sql").read_text(encoding="utf-8")
        rows = re.findall(r"^\s*\('([^']+)',\s*'([^']+)',\s*'(\d{6})',\s*'([^']+)'\)", migration, re.MULTILINE)
        self.assertEqual(len(rows), 39)
        self.assertEqual(len({row[0] for row in rows}), 39)
        self.assertEqual(len({row[2] for row in rows}), 39)
        self.assertIn("market_sector_etfs_sector_name_uidx", migration)
        self.assertNotIn("글로벌AI사이버보안", migration)
        self.assertIn("do nothing", migration.lower())
        self.assertNotIn("do update set", migration.lower())

    def test_sector_flow_stores_open_close_and_server_rankings(self):
        migration = (ROOT / "supabase/migrations/20260827_add_sector_flow_prices.sql").read_text(encoding="utf-8")
        pipeline = (ROOT / "supabase/functions/sector-flow/index.ts").read_text(encoding="utf-8")
        scoring = (ROOT / "supabase/functions/_shared/sector-flow.ts").read_text(encoding="utf-8")
        self.assertIn("market_sector_etf_prices", migration)
        self.assertIn("market_sector_weekly_rankings", migration)
        self.assertIn('body.stage === "open"', pipeline)
        self.assertIn('body.stage === "close"', pipeline)
        self.assertIn("DATABASE_PAGE_SIZE = 1000", pipeline)
        self.assertIn("PRICE_RETENTION_WEEKS = 10", pipeline)
        self.assertIn("RANKING_RETENTION_WEEKS = 6", pipeline)
        self.assertIn("fetchKisDailyPrices(credentials, token, item.etf_ticker, priceStart, end)", pipeline)
        self.assertIn("getKisAccessToken(credentials, admin)", pipeline)
        self.assertIn("backfill_history === true", pipeline)
        self.assertIn('fetchKisEtfTopHoldings(credentials, token, item.etf_ticker, 3)', pipeline)
        self.assertIn('.delete().lt("market_date", retentionStart)', pipeline)
        self.assertIn('if (stage === "close" && !rebuildOnly)', pipeline)
        self.assertIn("body.rebuild_only === true", pipeline)
        self.assertIn("stitchRebuiltRankings", pipeline)
        self.assertIn('.eq("week_start", rankingRetentionStart)', pipeline)
        self.assertIn('await deleteQuery.eq("week_start", currentWeek)', pipeline)
        self.assertIn(".range(from, from + DATABASE_PAGE_SIZE - 1)", pipeline)
        self.assertIn("if (page.length < DATABASE_PAGE_SIZE) break", pipeline)
        self.assertIn("endpointPrice / baseline.closePrice", scoring)
        self.assertIn("fourWeekBaseline", scoring)
        self.assertIn("endpointPrice / fourWeekBaseline.closePrice", scoring)
        self.assertNotIn("latestPrice / baseline.closePrice", scoring)

    def test_new_sector_etf_registration_resolves_metadata_and_backfills_prices(self):
        admin_html = (ROOT / "admin.html").read_text(encoding="utf-8")
        admin_js = (ROOT / "admin.js").read_text(encoding="utf-8")
        control = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        kis = (ROOT / "supabase/functions/_shared/kis-client.ts").read_text(encoding="utf-8")

        self.assertIn('id="sector-name-input"', admin_html)
        self.assertIn('id="sector-etf-ticker-input"', admin_html)
        self.assertNotIn('id="sector-etf-name-input"', admin_html)
        self.assertNotIn('id="sector-etf-issuer-input"', admin_html)
        self.assertIn("sector_name: document.getElementById('sector-name-input').value", admin_js)
        self.assertIn("etf_ticker: document.getElementById('sector-etf-ticker-input').value", admin_js)
        self.assertIn("fetchKisDailyPriceBundle", control)
        self.assertIn("10 * 7 * 86_400_000", control)
        self.assertIn('body: JSON.stringify({ stage: "close", rebuild_only: true })', control)
        self.assertIn("issuerFromEtfName(bundle.instrumentName)", control)
        self.assertIn("hts_kor_isnm", kis)
        self.assertIn("fetchKisEtfTopHoldings", control)
        self.assertIn("etf_cnfg_issu_rlim", kis)
        self.assertIn('KIS_TOKEN_CACHE_KEY = "kis_access_token_prod"', kis)
        self.assertIn("TOKEN_EXPIRY_MARGIN_MS = 10 * 60_000", kis)
        self.assertIn('store.from("app_settings")', kis)
        self.assertIn("expires_in", kis)
        self.assertIn("getKisAccessToken(credentials, admin)", control)

    def test_news_prompt_remains_secret_driven(self) -> None:
        adapter = (ROOT / "supabase/functions/_shared/openai-adapter.ts").read_text(encoding="utf-8")
        self.assertIn('Deno.env.get("NEWS_ANALYSIS_SYSTEM_PROMPT")', adapter)
        self.assertIn('prompt.replace(/\\{\\{news_candidates\\}\\}/gi, "")', adapter)
        self.assertIn('{{EXTREME_SIGNAL_CRITERIA}}', adapter)
        self.assertNotIn('[미국 주식시장 파급 경로 보완]', adapter)

    def test_news_output_contract_excludes_articles_from_sentiment_counts(self) -> None:
        pipeline = (ROOT / "supabase/functions/news-pipeline/index.ts").read_text(encoding="utf-8")
        self.assertIn("outputs.filter((output) => !output.excludeFromIndex)", pipeline)
        self.assertIn("outputs.length - indexOutputs.length", pipeline)

    def test_market_context_has_both_disparity_directions(self) -> None:
        indicators = (ROOT / "supabase/functions/_shared/market-indicators.ts").read_text(encoding="utf-8")
        self.assertIn("disparity60_upside_widening", indicators)
        self.assertIn("disparity60_downside_widening", indicators)
        self.assertIn("bullish_stochastic_divergence", indicators)

    def test_news_sources_and_partial_failure_reporting_remain_enabled(self) -> None:
        pipeline = (ROOT / "supabase/functions/news-pipeline/index.ts").read_text(encoding="utf-8")
        for source in ('"yonhap"', '"maekyung"', '"financial_news"'):
            self.assertIn(source, pipeline)
        self.assertIn("Promise.allSettled", pipeline)
        self.assertIn("errors: results.flatMap", pipeline)

    def test_fomc_prompt_has_stability_boundaries(self) -> None:
        prompt = (ROOT / "supabase/prompts/fomc-policy-v1.2.txt").read_text(encoding="utf-8")
        self.assertIn("물가를 직접 억제하거나 가격안정을 회복하는 것이 인상의 핵심 목적이 아니고", prompt)
        self.assertIn("직전 정책 배경이 제공되지 않은 경우 not_confirmed가 아니라 uncertain", prompt)
        self.assertIn("reason_confidence가 0.55 미만이면 primary_reason=uncertain", prompt)
        self.assertIn("외부 입력이 성명문과 충돌하면 성명문을 우선", prompt)
        self.assertIn("같은 방향의 인상 또는 인하가 동결 없이 3회 이상 연속", prompt)
        self.assertIn("2회에서 동결로 끝나면 확정 추세가 아니라 단기 조정 종료", prompt)
        self.assertIn("징검다리 추세", prompt)
        self.assertIn("동결이 두 번 연속될 때 두 번째 동결", prompt)
        self.assertIn("같은 방향에서 primary_reason만 바뀐 결정은 새로운 금리 방향의 첫 결정이 아니며", prompt)
        self.assertIn("이를 직접 판정하거나 출력하지 않는다", prompt)
        self.assertNotIn("연속 인상 중 첫번째 동결", prompt)
        self.assertIn("목표 범위 자체의 폭을 이번 회의의 인상·인하 폭으로 해석", prompt)
        self.assertIn("성명문에 명시된 이번 회의의 인상·인하 폭", prompt)
        self.assertIn("1%p=100bp 기준으로 환산", prompt)
        self.assertIn("변동폭의 크기와 관계없이 인상은 양수, 인하는 음수", prompt)
        self.assertIn("외부 코드가 저장된 직전 목표금리와 이번 목표금리의 동일한 경계끼리 비교", prompt)
        self.assertNotIn("2008년 이전처럼", prompt)
        self.assertIn("[동결의 정책 문맥]", prompt)
        self.assertIn("FOMC 분석 프롬프트 v1.2", prompt)
        self.assertIn("normalization_hike", prompt)
        self.assertIn("normalization_cut", prompt)

    def test_fomc_pipeline_normalizes_ai_output_before_storage(self) -> None:
        pipeline = (ROOT / "supabase/functions/policy-pipeline/index.ts").read_text(encoding="utf-8")
        self.assertIn("const REASON_CONFIDENCE_THRESHOLD = 0.55", pipeline)
        self.assertIn("function normalizedChangeBps", pipeline)
        self.assertIn("const isFiniteNumber = (value: unknown): value is number", pipeline)
        self.assertIn("target_range_lower,target_range_upper,primary_reason", pipeline)
        self.assertIn("normalizeAnalysis(await analyzeStatement", pipeline)
        self.assertIn('body.mode === "score"', pipeline)
        self.assertIn("score_profile: POLICY_SCORE_PROFILE", pipeline)
        self.assertIn('not("policy_index", "is", null)', pipeline)
        self.assertIn("FOMC_POLICY_SYSTEM_PROMPT를 ${POLICY_PROMPT_VERSION} 원문으로 갱신", pipeline)

    def test_policy_admin_reviews_only_directional_decisions(self) -> None:
        policy_admin = (ROOT / "supabase/functions/_shared/policy-admin.ts").read_text(encoding="utf-8")
        self.assertIn('.neq("action", "hold")', policy_admin)
        self.assertIn('"uncertain"]', policy_admin)
        self.assertIn('String(rawScore).trim() === ""', policy_admin)
        self.assertNotIn('reason !== "uncertain" && !keyword', policy_admin)
        self.assertIn('review_type: row.meeting_date === latestDate ? "latest"', policy_admin)
        self.assertIn('row.admin_score_override ?? row.final_event_score', policy_admin)

    def test_admin_payload_cannot_override_api_action(self) -> None:
        admin_client = (ROOT / "admin.js").read_text(encoding="utf-8")
        frontend_core = (ROOT / "frontend-core.js").read_text(encoding="utf-8")
        policy_review = (ROOT / "admin-policy-review.js").read_text(encoding="utf-8")
        self.assertIn("functionClient.invoke('admin-control', { ...payload, action }", admin_client)
        self.assertIn("body: JSON.stringify(payload)", frontend_core)
        self.assertNotIn("action: article.dataset.policyAction", policy_review)

    def test_admin_registries_use_delete_without_activation_controls(self) -> None:
        admin_client = (ROOT / "admin.js").read_text(encoding="utf-8")
        admin_function = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        self.assertIn("data-delete-sector-id", admin_client)
        self.assertIn("data-delete-extreme-id", admin_client)
        self.assertNotIn("data-retire-sector-id", admin_client)
        self.assertNotIn("data-retire-extreme-id", admin_client)
        self.assertNotIn('data-sector-field="is_active"', admin_client)
        self.assertNotIn('data-extreme-field="is_active"', admin_client)
        self.assertIn('action === "delete_sector_etf"', admin_function)
        self.assertIn('action === "delete_extreme_news_rule"', admin_function)

    def test_all_site_inputs_disable_autocomplete_for_current_and_future_fields(self) -> None:
        helper = (ROOT / "autocomplete-off.js").read_text(encoding="utf-8")
        self.assertIn("function disableAutocomplete", helper)
        self.assertIn("new MutationObserver", helper)
        for page in ("index.html", "admin.html"):
            self.assertIn('autocomplete-off.js?v=1', (ROOT / page).read_text(encoding="utf-8"))

    def test_news_schedule_avoids_hour_boundary_and_logs_failed_response(self) -> None:
        workflow = (ROOT / ".github/workflows/news-pipeline.yml").read_text(encoding="utf-8")
        for cron in ('cron: "30 15 * * *"', 'cron: "50 15 * * *"', 'cron: "10 16 * * *"'):
            self.assertIn(cron, workflow)
        self.assertIn('cat "$response" >&2', workflow)
        self.assertLess(workflow.index('cat "$response" >&2'), workflow.index('news-pipeline request failed with HTTP'))

    def test_financial_stress_workflow_tracks_source_adapter(self) -> None:
        workflow = (ROOT / ".github/workflows/financial-stress.yml").read_text(encoding="utf-8")
        self.assertIn("backend/financial_stress_sources.py", workflow)

    def test_financial_news_source_is_allowed_by_database_constraint(self) -> None:
        initial = (ROOT / "supabase/migrations/20260824_article_sentiment_pipeline.sql").read_text(encoding="utf-8")
        upgrade = (ROOT / "supabase/migrations/20260827_allow_financial_news_source.sql").read_text(encoding="utf-8")
        for migration in (initial, upgrade):
            self.assertIn("'financial_news'", migration)
        self.assertIn("drop constraint if exists news_article_sentiments_source_name_check", upgrade)
        self.assertIn("add constraint news_article_sentiments_source_name_check", upgrade)


class EmergingIndexTests(unittest.TestCase):
    def test_index_uses_documented_weighted_components(self) -> None:
        week = "2026-08-21"
        raw = {
            "high_yield_oas": {week: 11.0},
            "em_dollar_index": {week: 130.0},
            "tail_risk_oas": {week: 11.5},
            "em_equity_volatility": {week: 30.0},
        }
        rows = em.build_rows(raw, date(2026, 8, 22), {week: 45.0})
        self.assertEqual(len(rows), 1)
        expected = sum(
            em.score(raw[key][week], *em.SCALES[key]) * weight
            for key, weight in em.WEIGHTS.items()
        )
        self.assertEqual(rows[0]["stress_index"], round(expected, 2))

    def test_auxiliary_volatility_does_not_block_main_index(self) -> None:
        week = "2026-08-21"
        raw = {
            "high_yield_oas": {week: 11.0},
            "em_dollar_index": {week: 130.0},
            "tail_risk_oas": {week: 11.5},
            "em_equity_volatility": {},
        }
        rows = em.build_rows(raw, date(2026, 8, 22), {week: 45.0})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["vxeem_4w_average"])
        self.assertFalse(rows[0]["is_provisional"])


if __name__ == "__main__":
    unittest.main()
