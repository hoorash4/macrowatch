"""Read-only operational health checks for scheduled MacroWatch collectors."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import requests

from common import SupabaseRest, require_env, request_with_retry


# The window reflects each job's schedule and normal publication cadence.  It
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
    "sector-flow.yml": 4, "small-business-risk.yml": 3,
    "earnings-us-automatic.yml": 4, "earnings-v2-korea-automatic.yml": 4,
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


def _github_date(value: object, label: str) -> date:
    if not value:
        raise ValueError(f"{label}이 없습니다.")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date()


def _github_run_date(run: dict) -> date:
    return _github_date(run.get("updated_at") or run.get("run_started_at") or run.get("created_at"), "실행 시각")


def check_workflows(today: date) -> list[str]:
    failures: list[str] = []
    try:
        token = require_env("GITHUB_TOKEN")
    except Exception as error:
        return [f"GitHub 예약 실행 검사 불가: {_message(error)}"]

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
                failures.append(f"예약 실행 실패 또는 미완료: {workflow} (status={status}, conclusion={conclusion})")
            elif (today - updated).days > max_age:
                failures.append(f"예약 실행 누락: {workflow}, latest={updated.isoformat()}")
        except Exception as error:
            # One broken/missing workflow must never prevent every other
            # collector and database series from being checked.
            failures.append(f"예약 실행 검사 오류: {workflow}: {_message(error)}")
    return failures


def _observation_date(value: object) -> date:
    text = str(value).strip()
    if not text:
        raise ValueError("빈 날짜")
    return date.fromisoformat(text[:10])


def check_database(today: date) -> list[str]:
    failures: list[str] = []
    try:
        db = SupabaseRest()
    except Exception as error:
        return [f"DB 최신값 검사 불가: {_message(error)}"]

    for label, (table, column, max_age) in DATABASE_SERIES.items():
        try:
            rows = db.request("GET", table, params={"select": column, "order": f"{column}.desc", "limit": "1"}) or []
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict) or not rows[0].get(column):
                failures.append(f"DB 최신값 없음: {label}")
                continue
            observed = _observation_date(rows[0][column])
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
