"""NFIB 중소기업 설문 원자료를 월별 위험 구성값으로 정규화한다."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import requests

from common import fetch_fred_observations


NFIB_PROCEDURE_URL = "https://api.nfib-sbet.org:443/rest/sbetdb/_proc/getTotals2"
NFIB_INDICATOR_URL = "https://api.nfib-sbet.org:443/rest/sbetdb/_proc/getIndicators2"
TIMEOUT_SECONDS = 45
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


def _post_rows(url: str, payload: dict[str, Any], label: str, session: requests.Session | None) -> list[dict[str, Any]]:
    client = session or requests.Session()
    response = None
    for attempt in range(4):
        response = client.post(url, json=payload, timeout=TIMEOUT_SECONDS)
        if response.status_code not in TRANSIENT_STATUSES or attempt == 3:
            break
        time.sleep(2 ** attempt)
    if response is None:
        raise RuntimeError("NFIB API가 응답하지 않았습니다.")
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError(f"NFIB {label} 응답 형식이 올바르지 않습니다.")
    return rows


def _base_params(start: date, end: date) -> list[dict[str, Any]]:
    return [
        {"name": "minYear", "param_type": "IN", "value": start.year},
        {"name": "minMonth", "param_type": "IN", "value": 1},
        {"name": "maxYear", "param_type": "IN", "value": end.year},
        {"name": "maxMonth", "param_type": "IN", "value": 12},
    ]


def _request_rows(question: str, start: date, end: date, session: requests.Session | None = None) -> list[dict[str, Any]]:
    payload = {
        "app_name": "sbet",
        "params": _base_params(start, end) + [
            {"name": "questions", "param_type": "IN", "value": question},
            {"name": "industry", "param_type": "IN", "value": ""},
            {"name": "employee", "param_type": "IN", "value": ""},
            {"name": "statev", "param_type": "IN", "value": ""},
        ],
    }
    return _post_rows(NFIB_PROCEDURE_URL, payload, question, session)


def _request_indicator_rows(indicator: str, start: date, end: date, session: requests.Session | None = None) -> list[dict[str, Any]]:
    payload = {
        "app_name": "sbet",
        "params": _base_params(start, end) + [
            {"name": "indicator", "param_type": "IN", "value": indicator},
        ],
    }
    return _post_rows(NFIB_INDICATOR_URL, payload, indicator, session)


def _month_key(raw_value: object) -> str | None:
    observed = None
    for date_format in ("%m/%d/%Y", "%Y/%m/%d"):
        try:
            observed = datetime.strptime(str(raw_value), date_format).date()
            break
        except ValueError:
            continue
    if observed is None:
        return None
    return observed.replace(day=1).isoformat()


def parse_answer_percentages(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, dict[int, float]]:
    """NFIB 응답 코드별 비율을 월 단위로 묶는다."""
    grouped: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        month = _month_key(row.get("monthyear"))
        if month is None or not start.replace(day=1).isoformat() <= month <= end.replace(day=1).isoformat():
            continue
        try:
            answer_code = int(row["resp_acode"])
            percentage = float(row["percent"])
        except (KeyError, TypeError, ValueError):
            continue
        grouped[month][answer_code] = percentage
    return dict(grouped)


def parse_indicator_values(rows: list[dict[str, Any]], indicator: str, start: date, end: date) -> dict[str, float]:
    """NFIB 지수 응답을 월별 값으로 정규화한다."""
    values: dict[str, float] = {}
    for row in rows:
        month = _month_key(row.get("monthyear"))
        if month is None or not start.replace(day=1).isoformat() <= month <= end.replace(day=1).isoformat():
            continue
        try:
            values[month] = float(row[indicator])
        except (KeyError, TypeError, ValueError):
            continue
    return values


def fetch_nfib_monthly(start: date, end: date) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """실질매출 전망, 차입난이도와 소기업낙관지수를 반환한다."""
    session = requests.Session()
    sales_answers = parse_answer_percentages(_request_rows("sales_expect", start, end, session), start, end)
    credit_answers = parse_answer_percentages(_request_rows("credit_access", start, end, session), start, end)
    optimism = parse_indicator_values(_request_indicator_rows("OPT_INDEX", start, end, session), "OPT_INDEX", start, end)
    sales_expectations = {
        month: answers.get(1, 0.0) + answers.get(2, 0.0) - answers.get(4, 0.0) - answers.get(5, 0.0)
        for month, answers in sales_answers.items()
        if any(code in answers for code in (1, 2, 4, 5))
    }
    borrowing_difficulty = {
        month: answers[3]
        for month, answers in credit_answers.items()
        if 3 in answers
    }
    return sales_expectations, borrowing_difficulty, optimism


def fetch_fred_monthly(series_id: str, api_key: str, start: date, end: date) -> dict[str, float]:
    """일별 FRED 관측값을 달력 월평균으로 집계한다."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for observation in fetch_fred_observations(
        series_id,
        api_key,
        start=start.isoformat(),
        end=end.isoformat(),
        timeout=TIMEOUT_SECONDS,
    ):
        raw_value, observed_on = observation.get("value"), observation.get("date")
        if raw_value in (None, ".") or not isinstance(observed_on, str):
            continue
        try:
            grouped[f"{observed_on[:7]}-01"].append(float(raw_value))
        except (TypeError, ValueError):
            continue
    return {month: sum(values) / len(values) for month, values in grouped.items() if values}
