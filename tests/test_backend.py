from __future__ import annotations

import sys
import types
import unittest
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch


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

import tracking.check_targets as check_targets  # noqa: E402
import common  # noqa: E402
import signals.em_stress_pipeline as em  # noqa: E402
import signals.em_capital_capacity_pipeline as em_capacity  # noqa: E402
import signals.financial_stress_pipeline as us  # noqa: E402
import signals.small_business_risk_pipeline as small_business  # noqa: E402
import sources.small_business_risk as small_business_source  # noqa: E402
import signals.korea_small_business_risk_pipeline as korea_small_business  # noqa: E402
import sources.korea_small_business_risk as korea_small_business_source  # noqa: E402
import signals.korea_stress_pipeline as kr  # noqa: E402
import signals.policy_expectation_pipeline as policy_expectation  # noqa: E402
import signals.equity_bond_model as equity_bond  # noqa: E402
import signals.equity_bond_pipeline as equity_bond_pipeline  # noqa: E402


class AutomationIsolationTests(unittest.TestCase):
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
        writable = database.automatic_rows(
            "example", rows, key="month", provisional="is_provisional"
        )
        self.assertEqual([row["month"] for row in writable], ["2026-07-01", "2026-08-01"])

    def test_scheduled_workflows_cannot_run_from_code_or_deploy_events(self) -> None:
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^  schedule:", workflow):
                self.assertNotRegex(workflow, r"(?m)^  push:", path.name)
                self.assertNotRegex(workflow, r"(?m)^  workflow_run:", path.name)
                self.assertNotRegex(workflow, r"(?m)^  repository_dispatch:", path.name)

    def test_backfill_entrypoints_are_removed(self) -> None:
        removed = (
            ".github/workflows/earnings-us-backfill.yml",
            ".github/workflows/earnings-us-universe-backfill.yml",
            ".github/workflows/earnings-v2-historical-batch.yml",
            ".github/workflows/earnings-v25-backfill.yml",
            "backend/earnings_us/backfill.py",
            "backend/earnings_us/backfill_cli.py",
            "backend/earnings_v2/pipeline.py",
            "backend/earnings_v2/cli.py",
        )
        for relative in removed:
            self.assertFalse((ROOT / relative).exists(), relative)
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("backfill", workflow, path.name)

    def test_scheduled_earnings_runs_still_force_database_writes(self) -> None:
        for name in ("earnings-us-automatic.yml", "earnings-v2-korea-automatic.yml"):
            workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertIn("github.event_name == 'schedule' && 'true'", workflow)
            self.assertIn('args+=(--write)', workflow)

    def test_schedule_only_commits_do_not_deploy_pages(self) -> None:
        workflow = (ROOT / ".github/workflows/pages-deploy.yml").read_text(encoding="utf-8")
        self.assertIn('paths-ignore:', workflow)
        self.assertIn('".github/workflows/**"', workflow)


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

    def test_target_alert_is_enqueued_once_per_active_channel(self) -> None:
        result = check_targets.CheckResult(
            target={"id": 3, "user_id": "11111111-1111-4111-8111-111111111111", "condition_type": "changed"},
            previous_value=Decimal("4.6"), current_value=Decimal("4.7"), should_alert=True,
        )

        class Database:
            def __init__(self) -> None:
                self.rows = []

            def request(self, method, table, **kwargs):
                if method == "GET" and table == "notification_channels":
                    return [
                        {"user_id": result.target["user_id"], "channel": "kakao_self", "config": {}, "is_active": True},
                        {"user_id": result.target["user_id"], "channel": "email", "config": {"address": "user@example.com"}, "is_active": True},
                    ]
                if method == "POST" and table == "alert_events":
                    self.rows = kwargs["body"]
                    return self.rows
                raise AssertionError((method, table, kwargs))

        db = Database()
        check_targets.enqueue_alerts(db, [result])
        self.assertEqual({row["channel"] for row in db.rows}, {"kakao_self", "email"})
        self.assertTrue(all(row["status"] == "pending" for row in db.rows))

    def test_email_target_alert_is_sent_and_marked_independently(self) -> None:
        event = {
            "id": 43, "target_id": 3, "user_id": "11111111-1111-4111-8111-111111111111",
            "previous_value": "4.6", "current_value": "4.7", "condition_type": "changed",
            "target_value": None, "channel": "email", "status": "pending", "attempt_count": 0,
            "created_at": "2026-08-29T00:00:00+00:00",
        }

        class Database:
            def __init__(self) -> None:
                self.patches = []

            def request(self, method, table, **kwargs):
                if method == "GET" and table == "alert_events":
                    return [event]
                if method == "GET" and table == "notification_channels":
                    return [{"user_id": event["user_id"], "config": {"address": "user@example.com"}}]
                if method == "PATCH" and table == "alert_events":
                    self.patches.append(kwargs)
                    return None
                raise AssertionError((method, table, kwargs))

        db = Database()
        with patch.object(check_targets, "send_email_alert") as send_email:
            delivered, failures = check_targets.deliver_queued_alerts(db, [{"id": 3, "title": "미국 10년물 국채 금리"}])
        self.assertEqual((delivered, failures), (1, 0))
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.args[0], "user@example.com")
        self.assertIn("4.6 → 4.7", send_email.call_args.args[1])
        self.assertEqual(db.patches[0]["body"]["status"], "sent")


class SharedCalculationTests(unittest.TestCase):
    def test_korea_msi_starts_only_when_the_first_official_fsi_exists(self) -> None:
        months = ["2023-01-01", "2023-02-01", "2023-03-01"]
        values = {
            key: {month: 1.0 for month in months}
            for key in kr.SERIES
        }
        rows = kr.build_monthly_rows(values, {"2023-02-01": 18.0}, date(2023, 3, 15))
        self.assertEqual([row["month"] for row in rows], ["2023-02-01", "2023-03-01"])
        self.assertFalse(rows[0]["is_provisional"])
        self.assertTrue(rows[1]["is_provisional"])


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

    def test_automation_schedule_save_reports_the_persisted_time(self):
        admin_ui = (ROOT / "assets/js/admin/admin.js").read_text(encoding="utf-8")
        schedule_names = (ROOT / "supabase/functions/admin-control/github.ts").read_text(encoding="utf-8")
        market_context = (ROOT / ".github/workflows/market-context.yml").read_text(encoding="utf-8")
        self.assertIn("update_automation_time", admin_ui)
        self.assertIn("showNotice('일정 저장 완료'", admin_ui)
        self.assertIn("실행 시간을 ${time}으로 저장했습니다.", admin_ui)
        self.assertIn('"market-context.yml": "뉴스 분석용 KOSPI 가격 수집"', schedule_names)
        self.assertIn('"check-targets.yml": "지표 추적 알림"', schedule_names)
        self.assertIn('cron: "0 7 * * 1-5"', market_context)
        self.assertNotIn('cron: "0 6 * * 1-5"', market_context)

    def test_scheduled_workflow_failure_email_is_centralized_and_complete(self):
        notifier = (ROOT / ".github/workflows/scheduled-failure-email.yml").read_text(encoding="utf-8")
        us_workflow = (ROOT / ".github/workflows/earnings-us-automatic.yml").read_text(encoding="utf-8")
        scheduled_names = set()
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^  schedule:", workflow):
                scheduled_names.add(re.search(r"(?m)^name:\s*(.+)$", workflow).group(1).strip())

        monitored_block = re.search(r"workflows:\n(?P<body>(?:\s+- .+\n)+)", notifier).group("body")
        monitored_names = set(re.findall(r'^\s+- "(.+)"$', monitored_block, flags=re.MULTILINE))

        self.assertEqual(monitored_names, scheduled_names)
        self.assertIn("workflow_dispatch:", notifier)
        self.assertIn("github.event_name == 'workflow_dispatch'", notifier)
        self.assertIn("[MacroWatch][테스트] 예약 실행 실패 알림", notifier)
        self.assertIn("github.event.workflow_run.event == 'schedule'", notifier)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", notifier)
        self.assertIn("github.event.workflow_run.conclusion == 'timed_out'", notifier)
        self.assertIn("EMAIL_ADMIN: ${{ secrets.EMAIL_ADMIN }}", notifier)
        self.assertIn("EMAIL_APP_KEY: ${{ secrets.EMAIL_APP_KEY }}", notifier)
        self.assertIn("EMAIL_RCV_ADDRESS: ${{ secrets.EMAIL_RCV_ADDRESS }}", notifier)
        self.assertIn('smtplib.SMTP("smtp.gmail.com", 587, timeout=30)', notifier)
        self.assertIn('message["To"] = recipient', notifier)
        self.assertIn("Failure notification email sent successfully.", notifier)
        self.assertNotIn("notify_failure:", us_workflow)
        self.assertNotIn("OUTLOOK_APP_PASSWORD", notifier)

    def test_closed_membership_keeps_passwords_in_supabase_auth(self):
        migration = (ROOT / "supabase/migrations/20260827_add_closed_membership_accounts.sql").read_text(encoding="utf-8")
        auth = (ROOT / "assets/js/core/auth.js").read_text(encoding="utf-8")
        admin = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        self.assertIn("add column if not exists username text", migration)
        self.assertIn("signInWithPassword", auth)
        self.assertIn('action === "create_member"', admin)
        self.assertNotIn("password text", migration.lower())
        self.assertIn("requires_reauthentication", admin)
        admin_ui = (ROOT / "assets/js/admin/admin.js").read_text(encoding="utf-8")
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
        admin_ui = (ROOT / "assets/js/admin/admin.js").read_text(encoding="utf-8")
        policy_ui = (ROOT / "assets/js/admin/admin-policy-review.js").read_text(encoding="utf-8")
        self.assertIn("data-collapsible-count", admin_ui)
        self.assertIn("normalizedCount > 0 ? 'text-yellow-300' : 'text-slate-400'", admin_ui)
        self.assertIn("badge.classList.remove('hidden')", admin_ui)
        self.assertNotIn('id="uncertain-news-list"', admin_html)
        self.assertNotIn("invokeAdmin('list_uncertain_news')", admin_ui)
        self.assertIn("item.review_type !== 'latest'", policy_ui)
        self.assertNotIn("item.review_type !== 'selected'", policy_ui)
        self.assertNotIn("details.open = true", policy_ui)
        self.assertNotIn("setListAttentionCount('member-list'", admin_ui)
        self.assertNotIn("setListAttentionCount('sector-etf-list'", admin_ui)
        self.assertNotIn("setListAttentionCount('extreme-news-rule-list'", admin_ui)

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
        scoring = (ROOT / "supabase/functions/_shared/market/sector-flow.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/sector-flow.yml").read_text(encoding="utf-8")
        self.assertIn("market_sector_etf_prices", migration)
        self.assertIn("market_sector_weekly_rankings", migration)
        self.assertIn('body.stage === "open"', pipeline)
        self.assertIn('body.stage === "intraday"', pipeline)
        self.assertIn('body.stage === "close"', pipeline)
        for schedule in ('10 0', '30 3', '40 6'):
            self.assertIn(f'cron: "{schedule} * * 1-5"', workflow)
        self.assertNotIn('cron: "30 0 * * 1-5"', workflow)
        self.assertNotIn('cron: "0 7 * * 1-5"', workflow)
        self.assertIn("github.event.schedule == '30 3 * * 1-5'", workflow)
        self.assertIn("DATABASE_PAGE_SIZE = 1000", pipeline)
        self.assertIn("PRICE_RETENTION_WEEKS = 10", pipeline)
        self.assertIn("RANKING_RETENTION_WEEKS = 6", pipeline)
        self.assertIn("KIS_REQUEST_INTERVAL_MS", (ROOT / "supabase/functions/_shared/market/kis-client.ts").read_text(encoding="utf-8"))
        self.assertNotIn("const KIS_RATE_LIMIT_RETRY_DELAYS_MS", pipeline)
        self.assertIn("createKisRequestRunner", pipeline)
        kis_client = (ROOT / "supabase/functions/_shared/market/kis-client.ts").read_text(encoding="utf-8")
        self.assertIn('message.includes("초당 거래건수를 초과")', kis_client)
        self.assertIn("KIS_RATE_LIMIT_RETRY_DELAYS_MS", kis_client)
        self.assertIn("fetchKisDailyPrices(credentials, token, item.etf_ticker, priceStart, end)", pipeline)
        self.assertIn("fetchKisEtfCurrentPrice(credentials, token, item.etf_ticker)", pipeline)
        self.assertIn('github.event.schedule == \'30 3 * * 1-5\' && \'intraday\'', workflow)
        self.assertIn("getKisAccessToken(credentials, admin)", pipeline)
        self.assertIn("incompletePriceHistoryIds", pipeline)
        self.assertIn("missingHistoryIds.has(item.id)", pipeline)
        self.assertIn("initialized_history_count", pipeline)
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
        self.assertIn("calculateSectorLeadership", scoring)
        self.assertIn("recentUpDays.length < 11", scoring)
        self.assertIn("recentMarketCumulative <= 0", scoring)
        self.assertIn("2 * strengthScore * persistenceScore / (strengthScore + persistenceScore)", scoring)
        self.assertIn('fetchKisDomesticIndexPrices(', pipeline)
        self.assertIn('leadership_score: row.leadershipScore', pipeline)
        self.assertIn("latest_price", intraday_migration)
        self.assertIn("price_stage in ('open', 'intraday', 'close')", intraday_migration)
        self.assertIn("--file supabase/migrations/20260828_add_sector_intraday_prices.sql", (ROOT / ".github/workflows/deploy-supabase.yml").read_text(encoding="utf-8"))
        leadership_migration = (ROOT / "supabase/migrations/20260907214752_add_sector_leadership_score.sql").read_text(encoding="utf-8")
        self.assertIn("leadership_score numeric(5, 2)", leadership_migration)
        self.assertIn("check (leadership_score between 0 and 100)", leadership_migration)
        self.assertIn("--file supabase/migrations/20260907214752_add_sector_leadership_score.sql", (ROOT / ".github/workflows/deploy-supabase.yml").read_text(encoding="utf-8"))

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
        admin_js = (ROOT / "assets/js/admin/admin.js").read_text(encoding="utf-8")
        control = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        kis = (ROOT / "supabase/functions/_shared/market/kis-client.ts").read_text(encoding="utf-8")

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
        sector_registry = (ROOT / "supabase/functions/admin-control/sector-registry.ts").read_text(encoding="utf-8")
        self.assertIn('body: JSON.stringify({ stage: "close", rebuild_only: true })', sector_registry)
        self.assertIn("issuerFromEtfName(bundle.instrumentName)", control)
        self.assertIn("hts_kor_isnm", kis)
        self.assertIn("fetchKisEtfTopHoldings", control)
        validation = (ROOT / "supabase/functions/admin-control/validation.ts").read_text(encoding="utf-8")
        self.assertIn("etf_ticker: validateEtfTicker(body.etf_ticker)", validation)
        self.assertIn("/^[A-Z0-9]{6}$/.test(ticker)", validation)
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
        self.assertIn("history_initialization_pending", control)
        self.assertIn("showNotice('섹터 ETF 등록 완료'", admin_js)
        self.assertIn('latest_price: price.close, price_stage: "close"', control)
        self.assertIn("const normalizedTicker = normalizeEtfTicker(ticker)", kis)
        self.assertIn("FID_INPUT_ISCD: normalizedTicker", kis)
        self.assertNotIn("if (!/^\\d{6}$/.test(ticker))", kis)

    def test_admin_cards_are_reorderable_and_saved_per_admin(self):
        admin_html = (ROOT / "admin.html").read_text(encoding="utf-8")
        admin_js = (ROOT / "assets/js/admin/admin.js").read_text(encoding="utf-8")
        order_js = (ROOT / "assets/js/admin/admin-card-order.js").read_text(encoding="utf-8")
        control = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")

        self.assertEqual(admin_html.count('data-admin-card-id='), 9)
        self.assertIn('assets/js/admin/admin-card-order.js?v=2', admin_html)
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
        admin_js = (ROOT / "assets/js/admin/admin.js").read_text(encoding="utf-8")
        control = (ROOT / "supabase/functions/admin-control/index.ts").read_text(encoding="utf-8")
        migration = (ROOT / "supabase/migrations/20260902213000_add_earnings_v2_manual_resolution.sql").read_text(encoding="utf-8")
        pipeline = (ROOT / "backend/earnings_v2/automatic.py").read_text(encoding="utf-8")

        self.assertIn("대기 상태가 되면 즉시 표시합니다", admin_html)
        self.assertIn("earnings-v2-pending-form", admin_js)
        self.assertIn('name="top_line"', admin_js)
        self.assertIn('name="operating_income"', admin_js)
        self.assertIn('name="net_income"', admin_js)
        self.assertIn("resolve_earnings_v2_pending", admin_js)
        self.assertIn('admin.rpc("earnings_v2_list_pending")', control)
        self.assertIn('admin.rpc("earnings_v2_resolve_pending"', control)
        self.assertNotIn('recalculate_only: "true"', control)
        self.assertIn("where q.is_pending and q.calculation_version >= 6", migration)
        self.assertNotIn("exists (\n    select 1\n    from earnings_v2.universe_members later", migration)
        self.assertIn("source = 'manual'", migration)
        self.assertIn("is_pending = false", migration)
        self.assertIn("old.source = 'manual' and new.source <> 'manual'", migration)
        self.assertIn('str(record.get("source") or "") == "manual"', pipeline)
        self.assertIn("def recalculate_quarter", pipeline)
        self.assertIn('"mode": "stored_recalculation"', pipeline)
        workflow = (ROOT / ".github/workflows/earnings-v2-korea.yml").read_text(encoding="utf-8")
        self.assertIn("python -m earnings_v2.recalculate_cli", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("github.event_name == 'schedule'", workflow)
        self.assertIn("--write", workflow)
        self.assertNotIn("diagnose_kosdaq_51_100", workflow)

    def test_earnings_v2_daily_collection_is_receipt_checkpointed(self):
        pipeline = (ROOT / "backend/earnings_v2/automatic.py").read_text(encoding="utf-8")
        automatic_cli = (ROOT / "backend/earnings_v2/automatic_cli.py").read_text(encoding="utf-8")
        automatic_workflow = (ROOT / ".github/workflows/earnings-v2-korea-automatic.yml").read_text(encoding="utf-8")
        providers = (ROOT / "backend/earnings_v2/providers.py").read_text(encoding="utf-8")
        repository = (ROOT / "backend/earnings_common/repository.py").read_text(encoding="utf-8")
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

    def test_earnings_v2_public_history_and_seasonal_refresh_start_at_2016(self):
        migration = (ROOT / "supabase/migrations/20260905090000_expand_earnings_chart_to_2016.sql").read_text(encoding="utf-8")

        self.assertEqual(migration.count("market_year >= 2016"), 5)
        self.assertNotIn("market_year >= 2019", migration)
        self.assertIn("create or replace function earnings_v2.refresh_market_seasonal_adjustment()", migration)
        self.assertIn("create or replace function public.earnings_v2_public_market_series", migration)
        self.assertIn("select earnings_v2.refresh_market_seasonal_adjustment();", migration)

    def test_backfill_replacement_uses_physical_market_period(self):
        migration = (ROOT / "supabase/migrations/20260906204500_replace_backfill_rows_by_market_period.sql").read_text(encoding="utf-8")

        self.assertIn("as incoming(company_id text, market_year integer, market_quarter smallint)", migration)
        self.assertIn("q.market_year = incoming.market_year", migration)
        self.assertIn("q.market_quarter = incoming.market_quarter", migration)
        self.assertNotIn("q.fiscal_year = incoming.fiscal_year", migration)

    def test_target_alerts_use_db_tokens_retry_queue_and_visible_failures(self):
        checker = (ROOT / "backend/tracking/check_targets.py").read_text(encoding="utf-8")
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

    def test_target_alerts_support_email_channel_with_user_scoped_settings(self):
        checker = (ROOT / "backend/tracking/check_targets.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/check-targets.yml").read_text(encoding="utf-8")
        settings = (ROOT / "supabase/functions/notification-settings/index.ts").read_text(encoding="utf-8")
        self.assertIn('channel == "email"', checker)
        self.assertIn("send_email_alert(recipient, message)", checker)
        self.assertIn("EMAIL_ADMIN", workflow)
        self.assertIn("EMAIL_APP_KEY", workflow)
        self.assertIn("auth.auth.getUser(jwt)", settings)
        self.assertIn('.eq("user_id", user.id).eq("channel", "email")', settings)

    def test_news_prompt_remains_secret_driven(self) -> None:
        adapter = (ROOT / "supabase/functions/_shared/news/openai-adapter.ts").read_text(encoding="utf-8")
        self.assertIn('Deno.env.get("NEWS_ANALYSIS_SYSTEM_PROMPT")', adapter)
        self.assertIn('prompt.replace(/\\{\\{news_candidates\\}\\}/gi, "")', adapter)
        self.assertIn('{{EXTREME_SIGNAL_CRITERIA}}', adapter)
        self.assertNotIn('[미국 주식시장 파급 경로 보완]', adapter)

    def test_news_output_contract_excludes_articles_from_sentiment_counts(self) -> None:
        pipeline = (ROOT / "supabase/functions/news-pipeline/index.ts").read_text(encoding="utf-8")
        self.assertIn("outputs.filter((output) => !output.excludeFromIndex)", pipeline)
        self.assertIn("outputs.length - indexOutputs.length", pipeline)

    def test_market_context_has_both_disparity_directions(self) -> None:
        indicators = (ROOT / "supabase/functions/_shared/market/market-indicators.ts").read_text(encoding="utf-8")
        self.assertIn("disparity60_upside_widening", indicators)
        self.assertIn("disparity60_downside_widening", indicators)
        self.assertIn("bullish_stochastic_divergence", indicators)

    def test_news_sources_and_partial_failure_reporting_remain_enabled(self) -> None:
        pipeline = (ROOT / "supabase/functions/news-pipeline/index.ts").read_text(encoding="utf-8")
        for source in ('yonhap', 'maekyung', 'financial_news'):
            self.assertIn(f'{source}: [', pipeline)
        self.assertIn('Object.keys(RSS_FEEDS)', pipeline)
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
        self.assertIn('const FOMC_MODEL = Deno.env.get("AI_MODEL_FOMC") || "gpt-5.6-terra"', pipeline)
        self.assertIn("model: FOMC_MODEL", pipeline)
        self.assertNotIn('Deno.env.get("AI_MODEL_STANDARD")', pipeline)
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
        scoring = (ROOT / "supabase/functions/_shared/policy/policy-scoring.ts").read_text(encoding="utf-8")
        admin = (ROOT / "supabase/functions/_shared/policy/policy-admin.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/central-bank-policy.yml").read_text(encoding="utf-8")
        self.assertIn('POLICY_SCORE_PROFILE = "fed-policy-v5"', scoring)
        self.assertIn("Math.sign(value) * Math.round(Math.abs(value))", scoring)
        self.assertIn("Math.sign(score) * Math.round(Math.abs(score))", admin)
        self.assertNotIn("workflow_run:", workflow)
        self.assertIn("options: [latest, score]", workflow)

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
        sender = (ROOT / "backend/operations/send_policy_briefing_alerts.py").read_text(encoding="utf-8")
        migration = (ROOT / "supabase/migrations/20260827_add_fomc_briefings.sql").read_text(encoding="utf-8")
        self.assertIn('const POLICY_PROMPT_VERSION = "v2.0"', pipeline)
        self.assertIn("source_state_hash", pipeline)
        self.assertIn("policy_briefing_alerts", pipeline)
        self.assertIn("primary key (central_bank, meeting_date, revision)", migration)
        self.assertIn("통화정책 시그널에 새로운 FOMC 브리핑이 등록되었습니다.", sender)
        self.assertIn("통화정책 시그널에 업데이트된 FOMC 브리핑이 등록되었습니다.", sender)
        self.assertIn("const selected = (await fedSources()).slice(-1)", pipeline)
        self.assertNotIn('body.mode === "recent"', pipeline)
        self.assertNotIn('body.mode === "backfill"', pipeline)

    def test_policy_admin_reviews_include_admin_selected_history(self) -> None:
        policy_admin = (ROOT / "supabase/functions/_shared/policy/policy-admin.ts").read_text(encoding="utf-8")
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

    def test_stress_pipelines_preserve_accumulated_history(self):
        us_pipeline = (ROOT / "backend/signals/financial_stress_pipeline.py").read_text(encoding="utf-8")
        korea_pipeline = (ROOT / "backend/signals/korea_stress_pipeline.py").read_text(encoding="utf-8")
        em_pipeline = (ROOT / "backend/signals/em_stress_pipeline.py").read_text(encoding="utf-8")

        for pipeline in (us_pipeline, korea_pipeline, em_pipeline):
            self.assertNotIn("RETENTION_MONTHS", pipeline)
            self.assertNotIn("month_start_months_ago", pipeline)
            self.assertNotIn("delete_before(", pipeline)
        self.assertIn("today = date.today()", korea_pipeline)
        self.assertIn("today_month = today.replace(day=1).isoformat()", korea_pipeline)

    def test_equity_liquidity_uses_country_specific_weekly_versions(self):
        pipeline = (ROOT / "backend/signals/liquidity_pipeline.py").read_text(encoding="utf-8")
        chart = (ROOT / "assets/js/charts/liquidity-chart.js").read_text(encoding="utf-8")
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('US_VERSION = "us-equity-environment-weekly-v2"', pipeline)
        self.assertIn('KR_VERSION = "kr-equity-environment-weekly-v1"', pipeline)
        self.assertIn('"frequency": "W", "method_version": version', pipeline)
        self.assertIn("weekly_smoothed_features", pipeline)
        self.assertIn("us-equity-environment-weekly-v2", chart)
        self.assertIn("kr-equity-environment-weekly-v1", chart)
        self.assertIn("최근 4주 평균", html)
        self.assertIn("한국 주식시장 자금환경", html)
        self.assertIn("assets/js/charts/liquidity-chart.js?v=21", html)

    def test_admin_payload_cannot_override_api_action(self) -> None:
        admin_client = (ROOT / "assets/js/admin/admin.js").read_text(encoding="utf-8")
        frontend_core = (ROOT / "assets/js/core/frontend-core.js").read_text(encoding="utf-8")
        policy_review = (ROOT / "assets/js/admin/admin-policy-review.js").read_text(encoding="utf-8")
        self.assertIn("functionClient.invoke('admin-control', { ...payload, action }", admin_client)
        self.assertIn("body: JSON.stringify(payload)", frontend_core)
        self.assertNotIn("action: article.dataset.policyAction", policy_review)

    def test_admin_registries_use_delete_without_activation_controls(self) -> None:
        admin_client = (ROOT / "assets/js/admin/admin.js").read_text(encoding="utf-8")
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
        helper = (ROOT / "assets/js/core/autocomplete-off.js").read_text(encoding="utf-8")
        self.assertIn("function disableAutocomplete", helper)
        self.assertIn("new MutationObserver", helper)
        for page in ("index.html", "admin.html"):
            self.assertRegex((ROOT / page).read_text(encoding="utf-8"), r'assets/js/core/autocomplete-off\.js\?v=\d+')

    def test_news_schedule_avoids_hour_boundary_and_logs_failed_response(self) -> None:
        workflow = (ROOT / ".github/workflows/news-pipeline.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "30 15 * * *"', workflow)
        self.assertNotIn('cron: "50 15 * * *"', workflow)
        self.assertNotIn('cron: "10 16 * * *"', workflow)
        self.assertIn('cat "$response" >&2', workflow)
        self.assertLess(workflow.index('cat "$response" >&2'), workflow.index('news-pipeline request failed with HTTP'))

    def test_financial_stress_workflow_tracks_source_adapter(self) -> None:
        workflow = (ROOT / ".github/workflows/financial-stress.yml").read_text(encoding="utf-8")
        self.assertNotIn("push:", workflow)
        self.assertIn("signals.financial_stress_pipeline --years 3", workflow)

    def test_small_business_workflow_is_schedule_only_and_incremental(self) -> None:
        workflow = (ROOT / ".github/workflows/small-business-risk.yml").read_text(encoding="utf-8")
        self.assertNotIn("push:", workflow)
        self.assertIn("signals.small_business_risk_pipeline --years 1", workflow)
        self.assertNotIn("--replace", workflow)
        self.assertIn('cron: "30 21 * * *"', workflow)

    def test_small_business_risk_uses_available_component_weights(self) -> None:
        sales = {"2023-08-01": -15.0, "2023-09-01": -15.0}
        borrowing = {"2023-08-01": 8.5, "2023-09-01": 8.5}
        optimism = {"2023-08-01": 91.2, "2023-09-01": 90.7}
        rows = small_business.build_rows(sales, borrowing, optimism, date(2026, 9, 9))
        borrowing_score = small_business.component_score(8.5, "borrowing_difficulty")
        sales_score = small_business.component_score(-15.0, "sales_expectation")
        self.assertEqual(rows[0]["risk_index"], round(borrowing_score * .6 + sales_score * .4, 2))
        self.assertEqual(rows[0]["optimism_index"], 91.2)
        self.assertEqual(rows[1]["risk_index"], round(borrowing_score * .6 + sales_score * .4, 2))
        self.assertNotIn("high_yield_oas_pct", rows[0])
        self.assertNotIn("includes_oas", rows[0])

    def test_nfib_answer_parser_builds_sales_net_and_harder_share(self) -> None:
        sales_rows = [
            {"monthyear": "1/1/2026", "resp_acode": code, "percent": value}
            for code, value in ((1, 10), (2, 20), (4, 4), (5, 6))
        ]
        credit_rows = [{"monthyear": "1/1/2026", "resp_acode": 3, "percent": 7.5}]
        optimism_rows = [{"monthyear": "2026/1/1", "OPT_INDEX": 98.7}]
        with patch.object(small_business_source, "_request_rows", side_effect=[sales_rows, credit_rows]), \
                patch.object(small_business_source, "_request_indicator_rows", return_value=optimism_rows):
            sales, borrowing, optimism = small_business_source.fetch_nfib_monthly(date(2026, 1, 1), date(2026, 1, 1))
        self.assertEqual(sales["2026-01-01"], 20.0)
        self.assertEqual(borrowing["2026-01-01"], 7.5)
        self.assertEqual(optimism["2026-01-01"], 98.7)


    def test_small_business_card_uses_common_chart_widths_and_liquidity_icons(self) -> None:
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        chart = (ROOT / "assets/js/charts/small-business-risk-chart.js").read_text(encoding="utf-8")
        self.assertIn("미국 중소기업 위험지수", html)
        self.assertIn("utils.lineWidths.primary", chart)
        self.assertIn("utils.lineWidths.comparison", chart)
        self.assertIn("NFIB 소기업낙관지수(역)", chart)
        self.assertIn("value - optimismDomain.min", chart)
        self.assertIn("utils.scrollableSvg", chart)
        self.assertIn("left: PADDING.left", chart)
        self.assertIn("right: PADDING.right", chart)
        self.assertNotIn("right: 52", chart)
        for title in ("미국 주식시장 자금환경", "한국 주식시장 자금환경"):
            self.assertRegex(html, rf"fa-money-bill-transfer[^<]*</i></span>\s*<h2[^>]*>{title}</h2>")

    def test_korea_small_business_risk_carries_lagging_components(self) -> None:
        raw = {
            "funding_outlook": {"2026-05-01": 77.0, "2026-06-01": 76.9},
            "utilization_sa": {"2026-04-01": 75.7, "2026-05-01": 75.9},
            "delinquency": {"2026-04-01": 0.90},
            "headline_outlook": {"2026-05-01": 77.6, "2026-06-01": 79.6},
        }
        rows = korea_small_business.build_rows(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["utilization_source_month"], "2026-05-01")
        self.assertEqual(rows[-1]["delinquency_source_month"], "2026-04-01")
        self.assertTrue(rows[-1]["is_provisional"])
        expected = (
            korea_small_business.component_score(76.9, "funding_outlook") * .35
            + korea_small_business.component_score(75.9, "utilization_sa") * .30
            + korea_small_business.component_score(.90, "delinquency") * .35
        )
        self.assertEqual(rows[-1]["risk_index"], round(expected, 2))
        self.assertEqual(rows[-1]["headline_outlook_sbhi"], 79.6)

    def test_kosis_parser_and_parameters_select_official_total_series(self) -> None:
        rows = [
            {"PRD_DE": "202601", "DT": "79.3"},
            {"PRD_DE": "202602", "DT": "79.5"},
            {"PRD_DE": "202603", "DT": "-"},
        ]
        parsed = korea_small_business_source.parse_kosis_rows(rows, date(2026, 1, 1), date(2026, 3, 1))
        self.assertEqual(parsed, {"2026-01-01": 79.3, "2026-02-01": 79.5})
        response = Mock(status_code=200)
        response.raise_for_status = Mock()
        response.json.return_value = rows
        session = Mock()
        session.get.side_effect = [korea_small_business_source.requests.Timeout(), response]
        with patch("sources.korea_small_business_risk.time.sleep"):
            korea_small_business_source.fetch_kosis_series(
                "funding_outlook", date(2026, 1, 1), date(2026, 3, 1), api_key="test", session=session
            )
        self.assertEqual(session.get.call_count, 2)
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["tblId"], "DT_D10116")
        self.assertEqual(params["objL1"], "15340a.a")
        self.assertEqual(params["prdSe"], "M")

    def test_ecos_parser_and_request_use_nationwide_sme_loan_series(self) -> None:
        rows = [{"TIME": "202501", "DATA_VALUE": "0.77"}, {"TIME": "202502", "DATA_VALUE": "0.84"}]
        self.assertEqual(
            korea_small_business_source.parse_ecos_delinquency_rows(rows, date(2025, 1, 1), date(2025, 2, 1)),
            {"2025-01-01": 0.77, "2025-02-01": 0.84},
        )
        response = Mock(status_code=200)
        response.raise_for_status = Mock()
        response.json.return_value = {"StatisticSearch": {"row": rows}}
        session = Mock()
        session.get.return_value = response
        korea_small_business_source.fetch_ecos_sme_delinquency(
            date(2025, 1, 1), date(2025, 2, 1), api_key="test", session=session
        )
        url = session.get.call_args.args[0]
        self.assertIn("/141Y005/M/202501/202502/R4AB12/X00", url)

    def test_kbiz_parser_separates_concatenated_one_decimal_series(self) -> None:
        report = korea_small_business_source.parse_kbiz_report(
            """2022년 12월 전망
            (전산업) 중소기업 12월 업황전망 SBHI는 81.7로 전월대비 0.6p 하락
            전산업 경기전망 항목 자 금 사 정79.2 79.779.180.383.380.578.8△1.7△0.4수준판단
            중소제조업 평균가동률(2022. 10월)
            중소제조업(계절조정) -70.472.372.572.071.571.60.11.2소 기 업
            """
        )
        self.assertEqual(report["headline_outlook"], ("2022-12-01", 81.7))
        self.assertEqual(report["funding_outlook"], ("2022-12-01", 78.8))
        self.assertEqual(report["utilization_sa"], ("2022-10-01", 71.6))

    def test_kbiz_hwp_attachment_uses_official_viewer_parameters(self) -> None:
        markup = """
        <li><em>2020년 8월 결과보고서.hwp</em>
        <a data-ds="bbsAttachFile" data-seq="64799"
           onclick="fileViwer(this.getAttribute('data-ds'), this.getAttribute('data-seq'), '')">바로보기</a></li>
        """
        self.assertEqual(
            korea_small_business_source._kbiz_hwp_viewer_attachments(markup),
            [("bbsAttachFile", "64799")],
        )



    def test_korea_small_business_card_reuses_common_graph_form(self) -> None:
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        chart = (ROOT / "assets/js/charts/korea-small-business-risk-chart.js").read_text(encoding="utf-8")
        self.assertLess(html.index('id="korea-stress-dashboard"'), html.index('id="korea-small-business-risk-dashboard"'))
        self.assertLess(html.index('id="korea-small-business-risk-dashboard"'), html.index('id="korea-foreign-flow-dashboard"'))
        self.assertIn("utils.lineWidths.primary", chart)
        self.assertIn("utils.lineWidths.comparison", chart)
        self.assertIn("utils.scrollableSvg", chart)
        self.assertIn("left: PADDING.left", chart)
        self.assertIn("right: PADDING.right", chart)
        self.assertNotIn("right: 52", chart)
        self.assertIn("utils.scrollToLatest", chart)
        self.assertIn("중소기업 경기전망 SBHI(역)", chart)
        self.assertIn("value - headlineDomain.min", chart)
        self.assertIn('stroke-dasharray="5 4"', chart)
        self.assertIn("utils.monotonePathSegments", chart)
        self.assertNotIn("rows.filter(row => row.is_provisional)", chart)
        workflow = (ROOT / ".github/workflows/korea-small-business-risk.yml").read_text(encoding="utf-8")
        self.assertIn("KOSIS_API_KEY", workflow)
        self.assertIn("ECOS_API_KEY", workflow)
        self.assertIn("--years 1", workflow)
        self.assertNotIn("--replace", workflow)
        self.assertNotIn("workflow_run:", workflow)
        deploy_workflow = (ROOT / ".github/workflows/deploy-supabase.yml").read_text(encoding="utf-8")
        self.assertIn("20260909141525_add_kr_small_business_risk.sql", deploy_workflow)
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
        scoring = (ROOT / "supabase/functions/_shared/market/korea-foreign-flow.ts").read_text(encoding="utf-8")
        pipeline = (ROOT / "supabase/functions/korea-foreign-flow/index.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/korea-foreign-flow.yml").read_text(encoding="utf-8")
        self.assertIn("foreignNetBuyAmount / row.kospiTradingValue", scoring)
        self.assertIn("-(row.usdkrwRate / previousRate - 1)", scoring)
        self.assertIn("(flowZ + wonZ) / 2", scoring)
        self.assertIn("RETENTION_YEARS = 5", pipeline)
        self.assertIn('cron: "20 7 * * 1-5"', workflow)


if __name__ == "__main__":
    unittest.main()
