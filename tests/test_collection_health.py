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
        healthy_run = {
            "updated_at": "2026-09-11T00:00:00Z",
            "status": "completed",
            "conclusion": "success",
        }
        with (
            patch.object(health, "WORKFLOWS", workflows),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_state", return_value="active"),
            patch.object(health, "github_latest_run", side_effect=[None, healthy_run]) as latest,
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertEqual(["예약 실행 기록 없음: missing.yml"], failures)
        self.assertEqual(2, latest.call_count)

    def test_disabled_workflow_is_intentionally_skipped(self):
        with (
            patch.object(health, "WORKFLOWS", {"disabled.yml": 3}),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_state", return_value="disabled_manually"),
            patch.object(health, "github_latest_run") as latest,
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertEqual([], failures)
        latest.assert_not_called()

    def test_workflow_api_or_date_error_is_isolated_per_workflow(self):
        workflows = {"broken.yml": 3, "bad-date.yml": 3, "healthy.yml": 3}
        runs = [
            {"updated_at": "not-a-date", "status": "completed", "conclusion": "success"},
            {"updated_at": "2026-09-11T00:00:00Z", "status": "completed", "conclusion": "success"},
        ]
        with (
            patch.object(health, "WORKFLOWS", workflows),
            patch.object(health, "require_env", return_value="token"),
            patch.object(health, "github_workflow_state", side_effect=[RuntimeError("API down"), "active", "active"]),
            patch.object(health, "github_latest_run", side_effect=runs),
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertEqual(2, len(failures))
        self.assertIn("예약 실행 검사 오류: broken.yml: API down", failures)
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
            patch.object(health, "github_workflow_state", return_value="active"),
            patch.object(health, "github_latest_run", side_effect=runs),
        ):
            failures = health.check_workflows(date(2026, 9, 11))
        self.assertIn("예약 실행 실패 또는 미완료: running.yml (status=in_progress, conclusion=None)", failures)
        self.assertIn("예약 실행 시각 이상: future.yml, latest=2026-09-12", failures)

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
