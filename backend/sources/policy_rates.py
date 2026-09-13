"""Stored U.S. FOMC decision rates and official Korean policy-rate adapter."""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

from common import request_with_retry, require_env


KR_POLICY_STAT_CODE = "722Y001"
KR_POLICY_ITEM_CODE = "0101000"


def _number(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {".", "-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_us_policy_rate_chart_rows(
    db: Any,
    start: date,
    end: date,
    *,
    recent_limit: int | None = None,
) -> list[dict[str, object]]:
    """Project completed stored FOMC decisions to the shared chart read model."""
    saved = db.request("GET", "central_bank_policy_events", params={
        "select": "meeting_date,target_range_lower,target_range_upper",
        "central_bank": "eq.fed",
        "analysis_status": "eq.completed",
        "target_range_lower": "not.is.null",
        "target_range_upper": "not.is.null",
        "meeting_date": f"gte.{start.isoformat()}",
        "and": f"(meeting_date.lte.{end.isoformat()})",
        "order": "meeting_date.desc" if recent_limit else "meeting_date.asc",
        "limit": str(recent_limit or 10000),
    }) or []
    rows: list[dict[str, object]] = []
    for row in saved:
        try:
            observed = date.fromisoformat(str(row.get("meeting_date") or ""))
        except ValueError:
            raise RuntimeError(f"Stored FOMC decision has an invalid date: {row.get('meeting_date') or '(missing date)'}")
        lower, upper = _number(row.get("target_range_lower")), _number(row.get("target_range_upper"))
        if lower is None or upper is None or lower > upper:
            raise RuntimeError(f"Stored FOMC decision has invalid rate bounds: {observed.isoformat()}")
        rows.append({
            "series_code": "US_POLICY_RATE_MID",
            "observation_date": observed.isoformat(),
            "value": round((lower + upper) / 2, 4),
            "frequency": "E",
            "source": "DB:central_bank_policy_events",
        })
    return sorted(rows, key=lambda item: str(item["observation_date"]))


def fetch_korea_policy_rate_rows(
    start: date,
    end: date,
    api_key: str | None = None,
) -> list[dict[str, object]]:
    """Return the monthly Bank of Korea base rate series from ECOS."""
    key = api_key or require_env("ECOS_API_KEY")
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/"
        f"{KR_POLICY_STAT_CODE}/M/{start:%Y%m}/{end:%Y%m}/{KR_POLICY_ITEM_CODE}"
    )
    response = request_with_retry(lambda: requests.get(url, timeout=45))
    response.raise_for_status()
    payload = response.json()
    source_rows = ((payload.get("StatisticSearch") or {}).get("row") or [])
    rows: list[dict[str, object]] = []
    for source in source_rows:
        raw_time = str(source.get("TIME") or "")
        value = _number(source.get("DATA_VALUE"))
        if len(raw_time) != 6 or value is None:
            continue
        try:
            observed = date(int(raw_time[:4]), int(raw_time[4:6]), 1)
        except ValueError:
            continue
        rows.append({
            "series_code": "KR_POLICY_RATE",
            "observation_date": observed.isoformat(),
            "value": round(value, 4),
            "frequency": "M",
            "source": f"ECOS:{KR_POLICY_STAT_CODE}/{KR_POLICY_ITEM_CODE}",
        })
    if not rows:
        raise RuntimeError("ECOS Korean policy-rate series returned no usable monthly values")
    return rows
