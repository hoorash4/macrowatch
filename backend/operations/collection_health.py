"""Read-only operational health checks for scheduled MacroWatch collectors."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

import requests

from common import SupabaseRest, require_env, request_with_retry


# The window reflects each job's schedule and normal publication cadence.  It
# is deliberately a monitoring threshold only: it never fills, recalculates,
# or replaces any data.
WORKFLOWS = {
    "central-bank-policy.yml": 3, "check-targets.yml": 3,
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


def github_latest_run(workflow: str, token: str) -> dict:
    response = request_with_retry(lambda: requests.get(
        f"https://api.github.com/repos/hoorash4/macrowatch/actions/workflows/{workflow}/runs",
        params={"event": "schedule", "per_page": 1},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=30,
    ))
    response.raise_for_status()
    runs = response.json().get("workflow_runs") or []
    if not runs:
        raise RuntimeError(f"예약 실행 기록 없음: {workflow}")
    return runs[0]


def check_workflows(today: date) -> list[str]:
    token = require_env("GITHUB_TOKEN")
    failures: list[str] = []
    for workflow, max_age in WORKFLOWS.items():
        run = github_latest_run(workflow, token)
        updated = datetime.fromisoformat(str(run["updated_at"]).replace("Z", "+00:00")).date()
        if run.get("conclusion") != "success":
            failures.append(f"예약 실행 실패 또는 미완료: {workflow} ({run.get('conclusion')})")
        elif (today - updated).days > max_age:
            failures.append(f"예약 실행 누락: {workflow}, latest={updated.isoformat()}")
    return failures


def check_database(today: date) -> list[str]:
    db = SupabaseRest()
    failures: list[str] = []
    for label, (table, column, max_age) in DATABASE_SERIES.items():
        rows = db.request("GET", table, params={"select": column, "order": f"{column}.desc", "limit": "1"}) or []
        if not rows or not rows[0].get(column):
            failures.append(f"DB 최신값 없음: {label}")
            continue
        observed = date.fromisoformat(str(rows[0][column])[:10])
        if (today - observed).days > max_age:
            failures.append(f"DB 최신값 지연: {label}, latest={observed.isoformat()}")
    return failures


def main() -> None:
    today = datetime.now(timezone.utc).date()
    failures = [*check_workflows(today), *check_database(today)]
    print(json.dumps({"checked_at": today.isoformat(), "failures": failures}, ensure_ascii=False))
    if failures:
        raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
