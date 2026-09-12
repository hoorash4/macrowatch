from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from operations import collection_health as health  # noqa: E402


class FakeDb:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, table, params=None):
        self.calls.append(table)
        value = self.responses[table]
        if isinstance(value, BaseException):
            raise value
        return value


class CollectionHealthTests(unittest.TestCase):
    def test_missing_schedule_run_is_reported_without_stopping_other_workflows(self):
        workflows = {"missing.yml": 3, "healthy.yml": 3}
        infos = [
            {"state": "active", "created_at": "2026-09-01T00:00:00Z"},
            {"state": "active", "created_at": "2026-09-01T00:00:00Z"},
        ]
        healthy_run = {
            "updated_at": "2026-09-11T00:00:00Z",
            "status": "completed",
            "conclusion": "success",
        }
        with (
            patch.object(health, "WORKFLOWS", workflows),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_info", side_effect=infos),
            patch.object(health, "github_latest_run", side_effect=[None, healthy_run]) as latest,
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertEqual(["예약 실행 기록 없음: missing.yml, created=2026-09-01"], failures)
        self.assertEqual(2, latest.call_count)

    def test_new_workflow_without_first_scheduled_run_gets_bounded_grace(self):
        with (
            patch.object(health, "WORKFLOWS", {"weekly.yml": 10}),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_info", return_value={"state": "active", "created_at": "2026-09-08T00:00:00Z"}),
            patch.object(health, "github_latest_run", return_value=None),
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertEqual([], failures)

    def test_disabled_workflow_is_intentionally_skipped(self):
        with (
            patch.object(health, "WORKFLOWS", {"disabled.yml": 3}),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_info", return_value={"state": "disabled_manually", "created_at": "2026-01-01T00:00:00Z"}),
            patch.object(health, "github_latest_run") as latest,
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertEqual([], failures)
        latest.assert_not_called()

    def test_metadata_api_failure_can_fall_back_to_valid_run_history(self):
        healthy_run = {
            "updated_at": "2026-09-11T00:00:00Z",
            "status": "completed",
            "conclusion": "success",
        }
        with (
            patch.object(health, "WORKFLOWS", {"metadata-broken.yml": 3}),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_info", side_effect=RuntimeError("metadata API down")),
            patch.object(health, "github_latest_run", return_value=healthy_run),
        ):
            self.assertEqual([], health.check_workflows(date(2026, 9, 11)))

    def test_workflow_api_and_bad_date_errors_are_isolated_per_workflow(self):
        workflows = {"broken.yml": 3, "bad-date.yml": 3, "healthy.yml": 3}
        infos = [
            RuntimeError("metadata API down"),
            {"state": "active", "created_at": "2026-01-01T00:00:00Z"},
            {"state": "active", "created_at": "2026-01-01T00:00:00Z"},
        ]
        runs = [
            RuntimeError("runs API down"),
            {"updated_at": "not-a-date", "status": "completed", "conclusion": "success"},
            {"updated_at": "2026-09-11T00:00:00Z", "status": "completed", "conclusion": "success"},
        ]
        with (
            patch.object(health, "WORKFLOWS", workflows),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_info", side_effect=infos),
            patch.object(health, "github_latest_run", side_effect=runs),
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertEqual(2, len(failures))
        self.assertIn("예약 실행 검사 오류: broken.yml: runs API down", failures)
        self.assertTrue(any(item.startswith("예약 실행 검사 오류: bad-date.yml:") for item in failures))

    def test_incomplete_and_future_workflow_runs_are_reported(self):
        workflows = {"running.yml": 3, "future.yml": 3}
        runs = [
            {"updated_at": "2026-09-11T00:00:00Z", "status": "in_progress", "conclusion": None},
            {"updated_at": "2026-09-12T00:00:00Z", "status": "completed", "conclusion": "success"},
        ]
        with (
            patch.object(health, "WORKFLOWS", workflows),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_info", return_value={"state": "active", "created_at": "2026-01-01T00:00:00Z"}),
            patch.object(health, "github_latest_run", side_effect=runs),
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertIn("예약 실행 실패 또는 미완료: running.yml (status=in_progress, conclusion=None)", failures)
        self.assertIn("예약 실행 시각 이상: future.yml, latest=2026-09-12", failures)

    def test_failed_schedule_is_recovered_only_by_later_manual_success_and_fresh_data(self):
        failed = {"updated_at": "2026-09-10T00:00:00Z", "status": "completed", "conclusion": "failure"}
        repair = {"updated_at": "2026-09-11T00:00:00Z", "status": "completed", "conclusion": "success"}
        db = FakeDb({"fresh_table": [{"day": "2026-09-11"}]})
        with (
            patch.object(health, "WORKFLOWS", {"recoverable.yml": 3}),
            patch.object(health, "WORKFLOW_DATABASE_SERIES", {"recoverable.yml": "fresh"}),
            patch.object(health, "DATABASE_SERIES", {"fresh": ("fresh_table", "day", 3)}),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_info", return_value={"state": "active", "created_at": "2026-01-01T00:00:00Z"}),
            patch.object(health, "github_latest_run", return_value=failed),
            patch.object(health, "github_manual_success_after", return_value=repair),
            patch.object(health, "SupabaseRest", return_value=db),
        ):
            self.assertEqual([], health.check_workflows(date(2026, 9, 11)))

    def test_manual_success_does_not_hide_failed_schedule_when_data_is_stale(self):
        failed = {"updated_at": "2026-09-10T00:00:00Z", "status": "completed", "conclusion": "failure"}
        repair = {"updated_at": "2026-09-11T00:00:00Z", "status": "completed", "conclusion": "success"}
        db = FakeDb({"stale_table": [{"day": "2026-08-01"}]})
        with (
            patch.object(health, "WORKFLOWS", {"recoverable.yml": 3}),
            patch.object(health, "WORKFLOW_DATABASE_SERIES", {"recoverable.yml": "fresh"}),
            patch.object(health, "DATABASE_SERIES", {"fresh": ("stale_table", "day", 3)}),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_info", return_value={"state": "active", "created_at": "2026-01-01T00:00:00Z"}),
            patch.object(health, "github_latest_run", return_value=failed),
            patch.object(health, "github_manual_success_after", return_value=repair),
            patch.object(health, "SupabaseRest", return_value=db),
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertEqual(["예약 실행 실패 또는 미완료: recoverable.yml (status=completed, conclusion=failure)"], failures)

    def test_database_errors_bad_dates_and_future_dates_do_not_stop_remaining_checks(self):
        series = {
            "broken": ("broken_table", "day", 3),
            "bad_date": ("bad_date_table", "day", 3),
            "future": ("future_table", "day", 3),
            "healthy": ("healthy_table", "day", 3),
        }
        db = FakeDb({
            "broken_table": RuntimeError("db down"),
            "bad_date_table": [{"day": "bad"}],
            "future_table": [{"day": "2026-09-12"}],
            "healthy_table": [{"day": "2026-09-11"}],
        })
        with (
            patch.object(health, "DATABASE_SERIES", series),
            patch.object(health, "SupabaseRest", return_value=db),
        ):
            failures = health.check_database(date(2026, 9, 11))
        self.assertEqual(4, len(db.calls))
        self.assertIn("DB 최신값 검사 오류: broken: db down", failures)
        self.assertTrue(any(item.startswith("DB 최신값 검사 오류: bad_date:") for item in failures))
        self.assertIn("DB 최신값 날짜 이상: future, latest=2026-09-12", failures)
        self.assertFalse(any("healthy" in item for item in failures))

    def test_sector_flow_health_checks_each_stage_with_its_own_filter(self):
        series = {
            "sector_flow_open": ("market_sector_weekly_rankings", "calculated_at", 4, {"price_stage": "eq.open"}),
            "sector_flow_intraday": ("market_sector_weekly_rankings", "calculated_at", 4, {"price_stage": "eq.intraday"}),
            "sector_flow_close": ("market_sector_weekly_rankings", "calculated_at", 4, {"price_stage": "eq.close"}),
        }

        class SectorDb:
            def __init__(self):
                self.filters = []

            def request(self, method, table, params=None):
                self.filters.append(params["price_stage"])
                latest = {
                    "eq.open": "2026-09-11T00:10:00+00:00",
                    "eq.intraday": "2026-09-11T03:30:00+00:00",
                    "eq.close": "2026-09-01T06:40:00+00:00",
                }[params["price_stage"]]
                return [{"calculated_at": latest}]

        db = SectorDb()
        with (
            patch.object(health, "DATABASE_SERIES", series),
            patch.object(health, "SupabaseRest", return_value=db),
        ):
            failures = health.check_database(date(2026, 9, 11))
        self.assertEqual(["eq.open", "eq.intraday", "eq.close"], db.filters)
        self.assertEqual(["DB 최신값 지연: sector_flow_close, latest=2026-09-01"], failures)

    def test_missing_environment_is_reported_instead_of_raising(self):
        with patch.object(health, "require_env", side_effect=RuntimeError("GITHUB_TOKEN missing")):
            self.assertEqual(
                ["GitHub 예약 실행 검사 불가: GITHUB_TOKEN missing"],
                health.check_workflows(date(2026, 9, 11)),
            )
        with patch.object(health, "SupabaseRest", side_effect=RuntimeError("SUPABASE_URL missing")):
            self.assertEqual(
                ["DB 최신값 검사 불가: SUPABASE_URL missing"],
                health.check_database(date(2026, 9, 11)),
            )


if __name__ == "__main__":
    unittest.main()
