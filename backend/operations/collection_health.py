"""Read-only operational health checks for scheduled MacroWatch collectors."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import requests

from common import SupabaseRest, require_env, request_with_retry


# The window reflects each job's schedule and normal publication cadence. It
# is deliberately a monitoring threshold only: it never fills, recalculates,
# or replaces any data.
WORKFLOWS = {
    "central-bank-policy.yml": 3, "check-targets.yml": 3,
    "economic-chart-data.yml": 3,
    "em-capital-capacity.yml": 3, "em-stress.yml": 3,
    "equity-bond-attractiveness.yml": 10, "equity-bond-relative-value.yml": 40,
    "financial-stress.yml": 3, "inflation-model.yml": 3,
    "korea-foreign-flow.yml": 4, "korea-small-business-risk.yml": 3,
    "korea-stress.yml": 3, "liquidity.yml": 3, "market-context.yml": 4,
    "news-pipeline.yml": 3, "policy-expectation.yml": 3,
    "small-business-risk.yml": 3,
    "earnings-us-automatic.yml": 4, "earnings-us-edgar-automatic.yml": 4,
    "earnings-v2-korea-automatic.yml": 4, "earnings-v2-korea-kis-automatic.yml": 4,
}
DATABASE_SERIES = {
    "us_small_business_risk_monthly": ("us_small_business_risk_monthly", "month", 100),
    "kr_small_business_risk_monthly": ("kr_small_business_risk_monthly", "month", 100),
    "us_credit_stress_monthly": ("us_credit_stress_monthly", "month", 100),
    "us_market_tension_weekly": ("us_market_tension_weekly", "week", 21),
    "em_market_stress_weekly": ("em_market_stress_weekly", "week", 21),
    "em_capital_capacity_daily": ("em_capital_capacity_daily", "observation_date", 14),
    "equity_bond_attractiveness_weekly": ("equity_bond_attractiveness_weekly", "observation_date", 21),
    "liquidity_indices": ("liquidity_indices", "observation_date", 21),
    "sector_flow_rankings": ("market_sector_weekly_rankings", "calculated_at", 4),
}

# A successful manual run resolves a failed scheduled run only when there is
# independent data evidence that the collector recovered. Workflows without a
# suitable freshness series stay failed rather than trusting a green manual run.
WORKFLOW_DATABASE_SERIES = {
    "small-business-risk.yml": "us_small_business_risk_monthly",
    "korea-small-business-risk.yml": "kr_small_business_risk_monthly",
    "financial-stress.yml": "us_credit_stress_monthly",
    "em-stress.yml": "em_market_stress_weekly",
    "em-capital-capacity.yml": "em_capital_capacity_daily",
    "equity-bond-attractiveness.yml": "equity_bond_attractiveness_weekly",
    "liquidity.yml": "liquidity_indices",
}


def _message(error: BaseException) -> str:
    text = str(error).strip()
    return text or error.__class__.__name__


def _github_get(path: str, token: str, *, params: dict[str, object] | None = None) -> dict:
    response = request_with_retry(lambda: requests.get(
        f"https://api.github.com/repos/hoorash4/macrowatch/{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30,
    ))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub API 응답 형식이 올바르지 않습니다.")
    return payload


def github_workflow_info(workflow: str, token: str) -> dict:
    return _github_get(f"actions/workflows/{workflow}", token)


def github_latest_run(workflow: str, token: str) -> dict | None:
    payload = _github_get(
        f"actions/workflows/{workflow}/runs",
        token,
        params={"event": "schedule", "per_page": 1},
    )
    runs = payload.get("workflow_runs") or []
    if not isinstance(runs, list):
        raise RuntimeError("GitHub 실행 기록 형식이 올바르지 않습니다.")
    return runs[0] if runs and isinstance(runs[0], dict) else None


def github_manual_success_after(workflow: str, token: str, failed_run: dict) -> dict | None:
    failed_at = _github_run_datetime(failed_run)
    payload = _github_get(
        f"actions/workflows/{workflow}/runs",
        token,
        params={"event": "workflow_dispatch", "per_page": 20},
    )
    runs = payload.get("workflow_runs") or []
    if not isinstance(runs, list):
        raise RuntimeError("GitHub 수동 실행 기록 형식이 올바르지 않습니다.")
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("status") == "completed" and run.get("conclusion") == "success" and _github_run_datetime(run) > failed_at:
            return run
    return None


def _github_datetime(value: object, label: str) -> datetime:
    if not value:
        raise ValueError(f"{label}이 없습니다.")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _github_date(value: object, label: str) -> date:
    return _github_datetime(value, label).date()


def _github_run_datetime(run: dict) -> datetime:
    return _github_datetime(run.get("updated_at") or run.get("run_started_at") or run.get("created_at"), "실행 시각")


def _github_run_date(run: dict) -> date:
    return _github_run_datetime(run).date()


def _observation_date(value: object) -> date:
    text = str(value).strip()
    if not text:
        raise ValueError("빈 날짜")
    return date.fromisoformat(text[:10])


def _series_latest_date(db: SupabaseRest, series_name: str) -> date:
    table, column, _max_age = DATABASE_SERIES[series_name]
    rows = db.request("GET", table, params={"select": column, "order": f"{column}.desc", "limit": "1"}) or []
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict) or not rows[0].get(column):
        raise RuntimeError(f"DB 최신값 없음: {series_name}")
    return _observation_date(rows[0][column])


def _series_is_fresh(db: SupabaseRest, series_name: str, today: date) -> bool:
    _table, _column, max_age = DATABASE_SERIES[series_name]
    observed = _series_latest_date(db, series_name)
    return observed <= today and (today - observed).days <= max_age


def _scheduled_failure_recovered(workflow: str, run: dict, token: str, today: date, db: SupabaseRest | None) -> tuple[bool, SupabaseRest | None]:
    series_name = WORKFLOW_DATABASE_SERIES.get(workflow)
    if not series_name:
        return False, db
    repair = github_manual_success_after(workflow, token, run)
    if repair is None:
        return False, db
    if db is None:
        db = SupabaseRest()
    return _series_is_fresh(db, series_name, today), db


def check_workflows(today: date) -> list[str]:
    failures: list[str] = []
    try:
        token = require_env("GITHUB_TOKEN")
    except Exception as error:
        return [f"GitHub 예약 실행 검사 불가: {_message(error)}"]

    recovery_db: SupabaseRest | None = None
    for workflow, max_age in WORKFLOWS.items():
        try:
            info: dict | None = None
            info_error: Exception | None = None
            try:
                info = github_workflow_info(workflow, token)
            except Exception as error:
                # Run history can still be sufficient to judge freshness. A
                # metadata-only API failure must not erase usable run evidence.
                info_error = error

            if info is not None:
                state = str(info.get("state") or "unknown")
                # A deliberately disabled workflow is not expected to have
                # fresh scheduled runs.
                if state.startswith("disabled_"):
                    continue
                if state != "active":
                    failures.append(f"예약 workflow 상태 이상: {workflow} ({state})")
                    continue

            run = github_latest_run(workflow, token)
            if run is None:
                if info is None:
                    detail = _message(info_error) if info_error else "workflow metadata unavailable"
                    failures.append(f"예약 실행 검사 오류: {workflow}: {detail}")
                    continue
                # Newly-created weekly/monthly workflows can legitimately have
                # no scheduled run until their first cadence arrives. Reuse the
                # same monitoring tolerance as a bounded first-run grace period.
                created = _github_date(info.get("created_at"), "workflow 생성 시각")
                if created > today:
                    failures.append(f"workflow 생성 시각 이상: {workflow}, created={created.isoformat()}")
                elif (today - created).days > max_age:
                    failures.append(f"예약 실행 기록 없음: {workflow}, created={created.isoformat()}")
                continue

            updated = _github_run_date(run)
            if updated > today:
                failures.append(f"예약 실행 시각 이상: {workflow}, latest={updated.isoformat()}")
                continue
            conclusion = run.get("conclusion")
            status = run.get("status")
            # Older mocked/legacy run payloads may omit status. A concrete
            # non-completed status is unhealthy; absent status is judged by
            # conclusion so existing monitoring contracts remain compatible.
            if (status is not None and status != "completed") or conclusion != "success":
                if status in (None, "completed"):
                    recovered, recovery_db = _scheduled_failure_recovered(workflow, run, token, today, recovery_db)
                    if recovered:
                        continue
                failures.append(f"예약 실행 실패 또는 미완료: {workflow} (status={status}, conclusion={conclusion})")
            elif (today - updated).days > max_age:
                failures.append(f"예약 실행 누락: {workflow}, latest={updated.isoformat()}")
        except Exception as error:
            # One broken/missing workflow must never prevent every other
            # collector and database series from being checked.
            failures.append(f"예약 실행 검사 오류: {workflow}: {_message(error)}")
    return failures


def check_database(today: date) -> list[str]:
    failures: list[str] = []
    try:
        db = SupabaseRest()
    except Exception as error:
        return [f"DB 최신값 검사 불가: {_message(error)}"]

    for label, (table, column, max_age) in DATABASE_SERIES.items():
        try:
            observed = _series_latest_date(db, label)
            if observed > today:
                failures.append(f"DB 최신값 날짜 이상: {label}, latest={observed.isoformat()}")
            elif (today - observed).days > max_age:
                failures.append(f"DB 최신값 지연: {label}, latest={observed.isoformat()}")
        except Exception as error:
            # A schema/API/value problem in one series is itself a health
            # failure, but the rest of the database checks must still run.
            failures.append(f"DB 최신값 검사 오류: {label}: {_message(error)}")
    return failures


def main() -> None:
    today = datetime.now(timezone.utc).date()
    failures: list[str] = []
    # Keep the two domains independent as a final safety net. Individual
    # checks are already fault tolerant, but an unforeseen bug in one domain
    # must not suppress the other domain's diagnostics.
    for label, checker in (("workflow", check_workflows), ("database", check_database)):
        try:
            failures.extend(checker(today))
        except Exception as error:  # pragma: no cover - last-resort containment
            failures.append(f"{label} health 검사 자체 오류: {_message(error)}")

    print(json.dumps({"checked_at": today.isoformat(), "failures": failures}, ensure_ascii=False))
    if failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
