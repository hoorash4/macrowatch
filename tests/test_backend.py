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

# 계산 테스트에는 네트워크가 필요 없지만, 운영 의존성이 설치된 환경에서는
# 실제 requests 패키지를 보존해 뒤에 수집기 테스트가 같은 프로세스에서
# 하위 모듈과 예외 타입을 정상적으로 불러오게 한다.
try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = object
    requests_stub.HTTPError = type("HTTPError", (Exception,), {})
    sys.modules["requests"] = requests_stub
if "openpyxl" not in sys.modules:
    sys.modules["openpyxl"] = types.ModuleType("openpyxl")

import check_targets  # noqa: E402
import common  # noqa: E402
import em_stress_pipeline as em  # noqa: E402
import em_capital_capacity_pipeline as em_capacity  # noqa: E402
import financial_stress_pipeline as us  # noqa: E402
import korea_stress_pipeline as kr  # noqa: E402
import policy_expectation_pipeline as policy_expectation  # noqa: E402
import equity_bond_model as equity_bond  # noqa: E402
import equity_bond_pipeline as equity_bond_pipeline  # noqa: E402


class TargetConditionTests(unittest.TestCase):
    def test_decimal_parser_handles_commas_and_parentheses(self) -> None:
        self.assertEqual(check_targets.parse_decimal("1,234.50"), Decimal("1234.50"))
        self.assertEqual(check_targets.parse_decimal("(12.5)"), Decimal("-12.5"))

    def test_crossing_conditions_use_previous_and_current_values(self) -> None:
        target = {"condition_type": "gte", "target_value": "10"}
        self.assertTrue(check_targets.condition_met(target, Decimal("9"), Decimal("11")))
        self.assertFalse(check_targets.condition_met(target, Decimal("11"), Decimal("12")))

    def test_failed_target_alert_is_retried_and_marked_sent(self) -> None:
        event = {
            "id": 42,
            "target_id": 3,
            "user_id": "11111111-1111-4111-8111-111111111111",
            "previous_value": "4.6",
            "current_value": "4.7",
            "condition_type": "changed",
            "target_value": None,
            "status": "failed",
            "attempt_count": 1,
            "created_at": "2026-08-29T00:00:00+00:00",
        }

        class Database:
            def __init__(self) -> None:
                self.patches = []

            def request(self, method, table, **kwargs):
                if method == "GET" and table == "alert_events":
                    return [event]
                if method == "PATCH" and table == "alert_events":
                    self.patches.append(kwargs)
                    return None
                raise AssertionError((method, table, kwargs))

            def invoke_function(self, name, body):
                self.assertion = (name, body)
                return {"sent": True}

        db = Database()
        delivered, failures = check_targets.deliver_queued_alerts(
            db,
            [{"id": 3, "title": "미국 10년물 국채 금리"}],
        )
        self.assertEqual((delivered, failures), (1, 0))
        self.assertEqual(db.assertion[0], "kakao-auth")
        self.assertIn("4.6 → 4.7", db.assertion[1]["text"])
        self.assertEqual(db.patches[0]["body"]["status"], "sent")
        self.assertEqual(db.patches[0]["body"]["attempt_count"], 2)


class SharedCalculationTests(unittest.TestCase):
    def test_em_capacity_is_equal_weighted_and_reverses_adverse_inputs(self) -> None:
        periods = [f"2026-{month:02d}-{day:02d}" for month in (1, 2, 3) for day in range(1, 29)][:61]
        rising = {period: float(index + 1) for index, period in enumerate(periods)}
        rows = em_capacity.build_rows({key: dict(rising) for key in em_capacity.SERIES})
        self.assertEqual(len(rows), 2)
        self.assertLess(rows[-1]["capacity_index"], 0)

    def test_em_capacity_carries_delayed_sources_and_marks_only_latest_tail_provisional(self) -> None:
        periods = [f"2026-{month:02d}-{day:02d}" for month in (1, 2, 3) for day in range(1, 29)][:61]
        daily = {period: float(index + 1) for index, period in enumerate(periods)}
        delayed = dict(list(daily.items())[:-1])
        rows = em_capacity.build_rows({
            "em_dollar_index": delayed,
            "real_yield_10y": daily,
            "us_high_yield_oas": daily,
            "nfci": delayed,
        })
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]["is_provisional"])
        self.assertTrue(rows[1]["is_provisional"])

    def test_policy_expectation_spread_uses_complete_dates_and_70_30_weights(self) -> None:
        rows = policy_expectation.build_rows({
            "treasury_3m_rate": {"2026-08-25": 3.50, "2026-08-26": 3.60},
            "treasury_2y_rate": {"2026-08-25": 3.25},
            "effr_rate": {"2026-08-25": 3.75, "2026-08-26": 3.75},
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["near_term_spread_bps"], -25.0)
        self.assertEqual(rows[0]["cycle_spread_bps"], -50.0)
        self.assertEqual(rows[0]["expectation_spread_bps"], -32.5)

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

    def test_equity_bond_features_use_fixed_calendar_lags(self) -> None:
        monthly = []
        month = date(2020, 1, 1)
        for index in range(60):
            current = equity_bond.shift_month(month, index)
            monthly.append(equity_bond.MonthlyInputs(
                month=current,
                spy_adjusted_close=100.0 + index * 2.0,
                tlt_adjusted_close=100.0 + index * 0.3,
                real_yield_10y=-0.5 + index * 0.02,
                yield_curve_10y_2y=-1.0 + index * 0.03,
                baa_spread=2.0 + index * 0.01,
                nfci_level=-0.2 + index * 0.005,
                source_through_date=current,
            ))
        rows = equity_bond.build_feature_rows(monthly)
        self.assertEqual(rows[0].month, date(2022, 12, 1))
        self.assertEqual(rows[0].target_end_month, date(2023, 12, 1))
        self.assertAlmostEqual(rows[0].features[3], 0.03)
        self.assertEqual(rows[-1].future_relative_return_pct, None)

    def test_equity_bond_walk_forward_purges_unfinished_labels(self) -> None:
        monthly = []
        start = date(2018, 1, 1)
        for index in range(72):
            month = equity_bond.shift_month(start, index)
            cycle_month = index % 24
            stock_level = 100.0 + (cycle_month if cycle_month <= 12 else 24 - cycle_month) * 5.0
            monthly.append(equity_bond.MonthlyInputs(
                month=month,
                spy_adjusted_close=stock_level,
                tlt_adjusted_close=100.0 * (1.002 ** index) * (1.01 if index % 5 < 2 else 0.995),
                real_yield_10y=float(index % 19) / 10.0,
                yield_curve_10y_2y=float((index % 13) - 6) / 10.0,
                baa_spread=2.0 + float(index % 11) / 20.0,
                nfci_level=float((index % 17) - 8) / 20.0,
                source_through_date=month,
            ))
        features = equity_bond.build_feature_rows(monthly)
        forecasts = equity_bond.walk_forward_forecasts(features, minimum_training_samples=12)
        self.assertTrue(forecasts)
        first = forecasts[0]
        self.assertLessEqual(equity_bond.shift_month(first.training_end_month, 12), first.month)
        self.assertAlmostEqual(first.stock_probability + (1.0 - first.stock_probability), 1.0)

    def test_equity_bond_source_storage_excludes_existing_dfii10_and_nfci(self) -> None:
        observed = {date(2026, 7, 31): 1.5}
        raw = {
            "spy_adjusted_close": observed,
            "tlt_adjusted_close": observed,
            "yield_curve_10y_2y": observed,
            "baa_spread": observed,
            "real_yield_10y": observed,
            "nfci_level": observed,
        }
        rows = equity_bond_pipeline.source_rows(raw, "2026-08-28T00:00:00+00:00")
        self.assertEqual({row["series_code"] for row in rows}, {
            "SPY_ADJUSTED_CLOSE", "TLT_ADJUSTED_CLOSE", "T10Y2Y", "BAA10Y",
        })


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
    def test_closed_membership_keeps_passwords_in_supabase_auth(self):
        migration = (ROOT / "supabase/migrations/20260827_add_closed_membership_accounts.sql").read_text(encoding="utf-8")
        auth = (ROOT / "auth.js").read_text(encoding="utf-8")
        admin = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        self.assertIn("add column if not exists username text", migration)
        self.assertIn("signInWithPassword", auth)
        self.assertIn('action === "create_member"', admin)
        self.assertNotIn("password text", migration.lower())
        self.assertIn("requires_reauthentication", admin)
        admin_ui = (ROOT / "admin.js").read_text(encoding="utf-8")
        admin_html = (ROOT / "admin.html").read_text(encoding="utf-8")
        self.assertIn("카카오 전용", admin_ui)
        self.assertIn("data-admin-credential", admin_ui)
        self.assertIn("이미 사용 중인 아이디입니다", admin)
        self.assertIn("const form = event.currentTarget", admin_ui)
        self.assertIn("member-row-grid", admin_ui)
        self.assertNotIn("md:grid-cols-[1fr_1fr_auto_auto_auto]", admin_ui)
        self.assertIn("form.member-row-grid", admin_html)
        self.assertIn("profile-username", auth)

    def test_collapsed_admin_lists_show_only_actionable_review_counts(self):
        admin_html = (ROOT / "admin.html").read_text(encoding="utf-8")
        admin_ui = (ROOT / "admin.js").read_text(encoding="utf-8")
        policy_ui = (ROOT / "admin-policy-review.js").read_text(encoding="utf-8")
        self.assertIn("data-collapsible-count", admin_ui)
        self.assertIn("normalizedCount > 0 ? 'text-yellow-300' : 'text-slate-400'", admin_ui)
        self.assertIn("badge.classList.remove('hidden')", admin_ui)
        self.assertIn("setListAttentionCount('uncertain-news-list', items.length)", admin_ui)
        self.assertIn("item.review_type !== 'latest'", policy_ui)
        self.assertNotIn("item.review_type !== 'selected'", policy_ui)
        self.assertNotIn("details.open = true", policy_ui)
        self.assertNotIn("setListAttentionCount('member-list'", admin_ui)
        self.assertNotIn("setListAttentionCount('sector-etf-list'", admin_ui)
        self.assertNotIn("setListAttentionCount('extreme-news-rule-list'", admin_ui)
        self.assertLess(admin_html.index('id="uncertain-news-list"'), admin_html.index('id="policy-review-list"'))

    def test_sector_registry_seed_is_bootstrap_only_and_never_replayed(self):
        migration = (ROOT / "supabase/migrations/20260827_seed_domestic_sector_etfs.sql").read_text(encoding="utf-8")
        deploy = (ROOT / ".github/workflows/deploy-supabase.yml").read_text(encoding="utf-8")
        rows = re.findall(r"^\s*\('([^']+)',\s*'([^']+)',\s*'(\d{6})',\s*'([^']+)'\)", migration, re.MULTILINE)
        self.assertEqual(len(rows), 39)
        self.assertEqual(len({row[0] for row in rows}), 39)
        self.assertEqual(len({row[2] for row in rows}), 39)
        self.assertIn("market_sector_etfs_sector_name_uidx", migration)
        self.assertNotIn("글로벌AI사이버보안", migration)
        self.assertIn("do nothing", migration.lower())
        self.assertNotIn("do update set", migration.lower())
        self.assertIn("ONE-TIME BOOTSTRAP ONLY", migration)
        self.assertNotIn("--file supabase/migrations/20260827_seed_domestic_sector_etfs.sql", deploy)

    def test_sector_flow_stores_open_intraday_close_and_server_rankings(self):
        migration = (ROOT / "supabase/migrations/20260827_add_sector_flow_prices.sql").read_text(encoding="utf-8")
        intraday_migration = (ROOT / "supabase/migrations/20260828_add_sector_intraday_prices.sql").read_text(encoding="utf-8")
        pipeline = (ROOT / "supabase/functions/sector-flow/index.ts").read_text(encoding="utf-8")
        scoring = (ROOT / "supabase/functions/_shared/sector-flow.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/sector-flow.yml").read_text(encoding="utf-8")
        self.assertIn("market_sector_etf_prices", migration)
        self.assertIn("market_sector_weekly_rankings", migration)
        self.assertIn('body.stage === "open"', pipeline)
        self.assertIn('body.stage === "intraday"', pipeline)
        self.assertIn('body.stage === "close"', pipeline)
        for schedule in ('10 0', '30 0', '30 3', '40 6', '0 7'):
            self.assertIn(f'cron: "{schedule} * * 1-5"', workflow)
        self.assertIn("github.event.schedule == '30 3 * * 1-5'", workflow)
        self.assertIn("DATABASE_PAGE_SIZE = 1000", pipeline)
        self.assertIn("PRICE_RETENTION_WEEKS = 10", pipeline)
        self.assertIn("RANKING_RETENTION_WEEKS = 6", pipeline)
        self.assertIn("KIS_REQUEST_INTERVAL_MS", (ROOT / "supabase/functions/_shared/kis-client.ts").read_text(encoding="utf-8"))
        self.assertNotIn("const KIS_RATE_LIMIT_RETRY_DELAYS_MS", pipeline)
        self.assertIn("createKisRequestRunner", pipeline)
        kis_client = (ROOT / "supabase/functions/_shared/kis-client.ts").read_text(encoding="utf-8")
        self.assertIn('message.includes("초당 거래건수를 초과")', kis_client)
        self.assertIn("KIS_RATE_LIMIT_RETRY_DELAYS_MS", kis_client)
        self.assertIn("fetchKisDailyPrices(credentials, token, item.etf_ticker, priceStart, end)", pipeline)
        self.assertIn("fetchKisEtfCurrentPrice(credentials, token, item.etf_ticker)", pipeline)
        self.assertIn('github.event.schedule == \'30 3 * * 1-5\' && \'intraday\'', workflow)
        self.assertIn("getKisAccessToken(credentials, admin)", pipeline)
        self.assertIn("backfill_history === true", pipeline)
        self.assertIn("incompletePriceHistoryIds", pipeline)
        self.assertIn("autoBackfillIds.has(item.id)", pipeline)
        self.assertIn("auto_backfill_count", pipeline)
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
        self.assertIn("return row.latestPrice", scoring)
        self.assertIn("latest_price", intraday_migration)
        self.assertIn("price_stage in ('open', 'intraday', 'close')", intraday_migration)
        self.assertIn("--file supabase/migrations/20260828_add_sector_intraday_prices.sql", (ROOT / ".github/workflows/deploy-supabase.yml").read_text(encoding="utf-8"))

    def test_sector_flow_has_database_cron_with_idempotent_retry_dispatcher(self):
        scheduler = (ROOT / "supabase/functions/sector-flow-scheduler/index.ts").read_text(encoding="utf-8")
        migration = (ROOT / "supabase/migrations/20260828_schedule_sector_flow.sql").read_text(encoding="utf-8")
        config = (ROOT / "supabase/config.toml").read_text(encoding="utf-8")
        deploy = (ROOT / ".github/workflows/deploy-supabase.yml").read_text(encoding="utf-8")

        self.assertIn('[functions.sector-flow-scheduler]', config)
        self.assertIn('verify_jwt = false', config.split('[functions.sector-flow-scheduler]', 1)[1].split('[functions.', 1)[0])
        self.assertIn('skipped: "outside_schedule_window"', scheduler)
        self.assertIn('skipped: "already_refreshed"', scheduler)
        self.assertIn('.gte("calculated_at", slotStartedAt.toISOString())', scheduler)
        self.assertIn('/functions/v1/sector-flow', scheduler)
        self.assertIn('{ name: "midday", stage: "intraday"', scheduler)
        self.assertIn('create extension if not exists pg_cron', migration)
        self.assertIn('create extension if not exists pg_net', migration)
        for schedule in ('10,25 0 * * 1-5', '30,45 3 * * 1-5', '40,55 6 * * 1-5'):
            self.assertIn(schedule, migration)
        self.assertIn('--file supabase/migrations/20260828_schedule_sector_flow.sql', deploy)

    def test_new_sector_etf_registration_resolves_metadata_and_backfills_prices(self):
        admin_html = (ROOT / "admin.html").read_text(encoding="utf-8")
        admin_js = (ROOT / "admin.js").read_text(encoding="utf-8")
        control = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        kis = (ROOT / "supabase/functions/_shared/kis-client.ts").read_text(encoding="utf-8")

        self.assertIn('id="sector-name-input"', admin_html)
        self.assertIn('id="sector-etf-ticker-input"', admin_html)
        self.assertIn('pattern="[A-Za-z0-9]{6}"', admin_html)
        self.assertNotIn('pattern="[0-9]{6}"', admin_html)
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
        self.assertIn("etf_ticker: validateEtfTicker(body.etf_ticker)", control)
        self.assertIn("/^[A-Z0-9]{6}$/.test(ticker)", control)
        self.assertIn("etf_cnfg_issu_rlim", kis)
        self.assertNotIn("hts_avls", kis)
        self.assertNotIn("!/^\\d{6}$/.test(holdingTicker)", kis)
        self.assertIn("Number.NEGATIVE_INFINITY", kis)
        raw_holdings = (ROOT / "supabase/migrations/20260827_allow_raw_sector_etf_holdings.sql").read_text(encoding="utf-8")
        self.assertIn("alter column holding_ticker drop not null", raw_holdings)
        self.assertIn("primary key (id)", raw_holdings)
        self.assertIn('KIS_TOKEN_CACHE_KEY = "kis_access_token_prod"', kis)
        self.assertIn("TOKEN_EXPIRY_MARGIN_MS = 10 * 60_000", kis)
        self.assertIn('store.from("app_settings")', kis)
        self.assertIn("expires_in", kis)
        self.assertIn("getKisAccessToken(credentials, admin)", control)
        self.assertIn("createKisRequestRunner", control)
        self.assertIn("runKisRequest(() => fetchKisDailyPriceBundle", control)
        self.assertIn("runKisRequest(() => fetchKisEtfTopHoldings", control)
        self.assertIn("incompletePriceHistoryIds", control)
        self.assertIn("history_backfill_pending", control)
        self.assertIn("showNotice('섹터 ETF 등록 완료'", admin_js)
        self.assertIn('latest_price: price.close, price_stage: "close"', control)
        self.assertIn("const normalizedTicker = normalizeEtfTicker(ticker)", kis)
        self.assertIn("FID_INPUT_ISCD: normalizedTicker", kis)
        self.assertNotIn("if (!/^\\d{6}$/.test(ticker))", kis)

    def test_admin_cards_are_reorderable_and_saved_per_admin(self):
        admin_html = (ROOT / "admin.html").read_text(encoding="utf-8")
        admin_js = (ROOT / "admin.js").read_text(encoding="utf-8")
        order_js = (ROOT / "admin-card-order.js").read_text(encoding="utf-8")
        control = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")

        self.assertEqual(admin_html.count('data-admin-card-id='), 11)
        self.assertIn('admin-card-order.js?v=2', admin_html)
        self.assertIn("initializeAdminCardOrder", admin_js)
        self.assertIn("get_admin_card_order", admin_js)
        self.assertIn("save_admin_card_order", admin_js)
        self.assertIn("admin-card-drag-handle", order_js)
        self.assertIn("container.insertBefore", order_js)
        self.assertIn("AUTO_SCROLL_EDGE_PX", order_js)
        self.assertIn("window.scrollBy", order_js)
        self.assertIn('action === "get_admin_card_order"', control)
        self.assertIn('action === "save_admin_card_order"', control)
        self.assertIn('`admin_card_order_${user.id}`', control)

    def test_earnings_v2_pending_rows_are_immediately_manually_resolvable(self):
        admin_html = (ROOT / "admin.html").read_text(encoding="utf-8")
        admin_js = (ROOT / "admin.js").read_text(encoding="utf-8")
        control = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        migration = (ROOT / "supabase/migrations/20260902213000_add_earnings_v2_manual_resolution.sql").read_text(encoding="utf-8")
        pipeline = (ROOT / "backend/earnings_v2/pipeline.py").read_text(encoding="utf-8")

        self.assertIn("대기 상태가 되면 즉시 표시합니다", admin_html)
        self.assertIn("earnings-v2-pending-form", admin_js)
        self.assertIn('name="top_line"', admin_js)
        self.assertIn('name="operating_income"', admin_js)
        self.assertIn('name="net_income"', admin_js)
        self.assertIn("resolve_earnings_v2_pending", admin_js)
        self.assertIn('admin.rpc("earnings_v2_list_pending")', control)
        self.assertIn('admin.rpc("earnings_v2_resolve_pending"', control)
        self.assertIn('recalculate_only: "true"', control)
        self.assertIn("where q.is_pending and q.calculation_version >= 6", migration)
        self.assertNotIn("exists (\n    select 1\n    from earnings_v2.universe_members later", migration)
        self.assertIn("source = 'manual'", migration)
        self.assertIn("is_pending = false", migration)
        self.assertIn("old.source = 'manual' and new.source <> 'manual'", migration)
        self.assertIn('str(record.get("source") or "") == "manual"', pipeline)
        self.assertIn("def recalculate_quarter", pipeline)
        self.assertIn('"mode": "stored_recalculation"', pipeline)
        workflow = (ROOT / ".github/workflows/earnings-v2-korea.yml").read_text(encoding="utf-8")
        self.assertIn("recalculate_only:", workflow)
        self.assertIn("args+=(--recalculate-only)", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("github.event_name == 'schedule'", workflow)
        self.assertIn("WRITE: ${{ inputs.write }}", workflow)
        self.assertIn('[[ "$WRITE" == "true" ]] && args+=(--write)', workflow)
        self.assertNotIn("diagnose_kosdaq_51_100", workflow)

    def test_earnings_v2_daily_collection_is_receipt_checkpointed(self):
        pipeline = (ROOT / "backend/earnings_v2/automatic.py").read_text(encoding="utf-8")
        automatic_cli = (ROOT / "backend/earnings_v2/automatic_cli.py").read_text(encoding="utf-8")
        automatic_workflow = (ROOT / ".github/workflows/earnings-v2-korea-automatic.yml").read_text(encoding="utf-8")
        providers = (ROOT / "backend/earnings_v2/providers.py").read_text(encoding="utf-8")
        repository = (ROOT / "backend/earnings_v2/repository.py").read_text(encoding="utf-8")
        migration = (ROOT / "supabase/migrations/20260902224500_add_earnings_v2_daily_checkpoint_read.sql").read_text(encoding="utf-8")
        initializer = (ROOT / "supabase/migrations/20260902225000_initialize_earnings_v2_daily_checkpoint.sql").read_text(encoding="utf-8")

        self.assertNotIn("timedelta(days=14)", pipeline)
        self.assertNotIn("recent_periodic_corp_codes", pipeline)
        self.assertIn('pipeline_state("daily_filings")', pipeline)
        self.assertIn("self.repository.pending_rows()", pipeline)
        self.assertIn('self.rpc("earnings_v2_list_pending"', repository)
        self.assertIn('"boundary_receipt_ids"', pipeline)
        self.assertIn("filing.receipt_no in boundary_receipts", pipeline)
        self.assertNotIn("def run_year", pipeline)
        self.assertIn("KoreaEarningsV2AutomaticPipeline", automatic_cli)
        self.assertIn("python -m earnings_v2.automatic_cli", automatic_workflow)
        self.assertIn('--phase', automatic_cli)
        self.assertIn('choices=("dart", "kis", "all")', automatic_cli)
        self.assertIn('args+=(--phase "$PHASE")', automatic_workflow)
        self.assertIn('cron: "30 10 * * 1-5"', automatic_workflow)
        self.assertIn('cron: "30 11 * * 1-5"', automatic_workflow)
        self.assertIn("github.event.schedule == '30 10 * * 1-5'", automatic_workflow)
        self.assertIn("EARNINGS_FINANCIAL_SOURCE_TOKEN", automatic_workflow)
        self.assertIn("DATA_GO_KR_SERVICE_KEY", automatic_workflow)
        self.assertNotIn("--year", automatic_workflow)
        self.assertNotIn("--quarter", automatic_workflow)
        self.assertNotIn("replace_company_quarters_for_backfill", pipeline)
        self.assertIn("def periodic_filings", providers)
        self.assertIn("result[receipt] = PeriodicFiling", providers)
        self.assertIn('self.rpc("earnings_v2_get_pipeline_state"', repository)
        self.assertIn("to service_role", migration)
        self.assertIn("'korea_v2', 'daily_filings'", initializer)
        self.assertIn("'last_checked_date', (now() at time zone 'Asia/Seoul')::date", initializer)
        self.assertIn("on conflict (source, operation) do nothing", initializer)

    def test_target_alerts_use_db_tokens_retry_queue_and_visible_failures(self):
        checker = (ROOT / "backend/check_targets.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/check-targets.yml").read_text(encoding="utf-8")
        kakao_auth = (ROOT / "supabase/functions/kakao-auth/index.ts").read_text(encoding="utf-8")
        migration = (ROOT / "supabase/migrations/20260829_make_target_alerts_retryable.sql").read_text(encoding="utf-8")
        self.assertIn('"status": "pending"', checker)
        self.assertIn('"status": "in.(pending,failed)"', checker)
        self.assertIn('"attempt_count": "lt.5"', checker)
        self.assertIn('db.invoke_function(', checker)
        self.assertIn('return 1 if notification_failures else 0', checker)
        self.assertNotIn("KAKAO_REFRESH_TOKEN", workflow)
        self.assertIn('action === "send_internal"', kakao_auth)
        self.assertIn("isServiceRoleRequest(request, serviceRoleKey)", kakao_auth)
        self.assertIn('payload.role === "service_role"', kakao_auth)
        self.assertIn("Date.now() + 5 * 60_000", kakao_auth)
        self.assertIn("delivery.status === 401", kakao_auth)
        self.assertIn("await persistRefresh(refreshed)", kakao_auth)
        self.assertIn("connected: false, last_error: message", kakao_auth)
        self.assertIn("'pending', 'sent', 'failed', 'skipped'", migration)

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

    def test_news_backfill_keeps_missing_collection_dates_separate(self) -> None:
        pipeline = (ROOT / "supabase/functions/news-pipeline/index.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/news-pipeline.yml").read_text(encoding="utf-8")
        self.assertIn("targetDate: parseTargetDate(body.target_date)", pipeline)
        self.assertIn("const runDate = targetDate || kstDate", pipeline)
        self.assertIn("previousCalendarDate(targetDate)", pipeline)
        self.assertIn("kstDate(candidate.publishedAt) === sourceDate", pipeline)
        self.assertIn("backfill_date:", workflow)
        self.assertIn('lookback_hours=360', workflow)

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
        self.assertIn("FOMC_POLICY_PROMPT_V2", pipeline)
        self.assertIn("briefing: analysis.briefing", pipeline)

    def test_fomc_scores_round_symmetrically_to_integers(self) -> None:
        scoring = (ROOT / "supabase/functions/_shared/policy-scoring.ts").read_text(encoding="utf-8")
        admin = (ROOT / "supabase/functions/_shared/policy-admin.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/central-bank-policy.yml").read_text(encoding="utf-8")
        self.assertIn('POLICY_SCORE_PROFILE = "fed-policy-v5"', scoring)
        self.assertIn("Math.sign(value) * Math.round(Math.abs(value))", scoring)
        self.assertIn("Math.sign(score) * Math.round(Math.abs(score))", admin)
        self.assertIn('workflows: ["Deploy Supabase changes"]', workflow)
        self.assertIn("github.event_name == 'workflow_run' && 'score'", workflow)

    def test_fomc_v2_prompt_preserves_policy_rules_and_adds_briefing_contract(self) -> None:
        original = (ROOT / "supabase/prompts/fomc-policy-v1.2.txt").read_text(encoding="utf-8")
        prompt = (ROOT / "supabase/prompts/fomc-policy-v2.0.txt").read_text(encoding="utf-8")
        original_rules = original.split("[출력 규칙]", 1)[0].replace("FOMC 분석 프롬프트 v1.2", "", 1).strip()
        self.assertIn(original_rules, prompt)
        self.assertIn("FOMC 분석 프롬프트 v2.0", prompt)
        self.assertIn('"briefing"', prompt)
        self.assertIn("previous_fomc_statement", prompt)
        self.assertIn("press_conference_transcript", prompt)
        self.assertIn("ai_overall_analysis", prompt)

    def test_fomc_briefing_alerts_are_idempotent_and_use_exact_messages(self) -> None:
        pipeline = (ROOT / "supabase/functions/policy-pipeline/index.ts").read_text(encoding="utf-8")
        sender = (ROOT / "backend/send_policy_briefing_alerts.py").read_text(encoding="utf-8")
        migration = (ROOT / "supabase/migrations/20260827_add_fomc_briefings.sql").read_text(encoding="utf-8")
        self.assertIn('const POLICY_PROMPT_VERSION = "v2.0"', pipeline)
        self.assertIn("source_state_hash", pipeline)
        self.assertIn("policy_briefing_alerts", pipeline)
        self.assertIn("primary key (central_bank, meeting_date, revision)", migration)
        self.assertIn("통화정책 시그널에 새로운 FOMC 브리핑이 등록되었습니다.", sender)
        self.assertIn("통화정책 시그널에 업데이트된 FOMC 브리핑이 등록되었습니다.", sender)
        self.assertIn('body.mode === "recent"', pipeline)
        self.assertIn('mode === "recent"', pipeline)
        self.assertIn("recentCutoff.setUTCFullYear", pipeline)
        self.assertIn('mode === "recent" && (!saved.briefing', pipeline)

    def test_policy_admin_reviews_include_admin_selected_history(self) -> None:
        policy_admin = (ROOT / "supabase/functions/_shared/policy-admin.ts").read_text(encoding="utf-8")
        self.assertIn('.neq("action", "hold")', policy_admin)
        self.assertIn('"uncertain"]', policy_admin)
        self.assertIn('String(rawScore).trim() === ""', policy_admin)
        self.assertNotIn('reason !== "uncertain" && !keyword', policy_admin)
        self.assertIn('selectedDates.includes(row.meeting_date) ? "selected"', policy_admin)
        self.assertIn("const rows = [latest, ...(selected || []), ...(unresolved || [])]", policy_admin)
        self.assertIn('row.meeting_date === latestDate ? "latest"', policy_admin)
        self.assertNotIn("slice(0, 20)", policy_admin)
        self.assertIn('그래프에서\n  // 선택한 과거 회의도 관리자가 직접 교정', policy_admin)
        self.assertIn('row.admin_score_override ?? row.final_event_score', policy_admin)

    def test_stress_pipelines_keep_only_thirty_seven_months(self):
        common = (ROOT / "backend/common.py").read_text(encoding="utf-8")
        us_pipeline = (ROOT / "backend/financial_stress_pipeline.py").read_text(encoding="utf-8")
        korea_pipeline = (ROOT / "backend/korea_stress_pipeline.py").read_text(encoding="utf-8")
        em_pipeline = (ROOT / "backend/em_stress_pipeline.py").read_text(encoding="utf-8")

        self.assertIn("def month_start_months_ago", common)
        self.assertIn("def delete_before", common)
        for pipeline in (us_pipeline, korea_pipeline, em_pipeline):
            self.assertIn("RETENTION_MONTHS = 37", pipeline)
            self.assertIn("month_start_months_ago(today, RETENTION_MONTHS)", pipeline)
        self.assertIn('delete_before("us_market_stress_index_monthly"', us_pipeline)
        self.assertIn('delete_before("us_market_tension_weekly"', us_pipeline)
        self.assertIn('delete_before("korea_market_stress_monthly"', korea_pipeline)
        self.assertIn('delete_before("korea_market_stress_weekly"', korea_pipeline)
        self.assertIn('"em_market_stress_weekly", "week", cutoff', em_pipeline)

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


class KoreaForeignFlowTests(unittest.TestCase):
    def test_pipeline_uses_normalized_equal_weight_components(self) -> None:
        scoring = (ROOT / "supabase/functions/_shared/korea-foreign-flow.ts").read_text(encoding="utf-8")
        pipeline = (ROOT / "supabase/functions/korea-foreign-flow/index.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/korea-foreign-flow.yml").read_text(encoding="utf-8")
        self.assertIn("foreignNetBuyAmount / row.kospiTradingValue", scoring)
        self.assertIn("-(row.usdkrwRate / previousRate - 1)", scoring)
        self.assertIn("(flowZ + wonZ) / 2", scoring)
        self.assertIn("RETENTION_YEARS = 5", pipeline)
        self.assertIn('cron: "0 8 * * 1-5"', workflow)


if __name__ == "__main__":
    unittest.main()
