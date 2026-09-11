"""Shared source/storage helpers for the economic-chart read model.

This module is deliberately not an executable collector. Scheduled automatic collection and
explicit historical backfill have separate entrypoints and may only share pure/source helpers.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import requests

from common import SupabaseRest, fetch_fred_observations, request_with_retry, require_env
from tracking.check_targets import CheckResult, condition_met, enqueue_alerts, json_number, parse_decimal


# Source contracts shared by automatic (short recent window) and explicit backfill
# (up to ten years or the provider's maximum available range).
FRED_SERIES = {
    "US2Y": ("DGS2", "D"),
    "US10Y": ("DGS10", "D"),
    "US10Y2Y": ("T10Y2Y", "D"),
    "HY_OAS": ("BAMLH0A0HYM2", "D"),
    "EM_OAS": ("BAMLEMCBPIOAS", "D"),
    "WTI": ("DCOILWTICO", "D"),
    "USDKRW": ("DEXKOUS", "D"),
    "RRP": ("RRPONTSYD", "D"),
    "TGA": ("WTREGEN", "W"),
    "WEI": ("WEI", "W"),
    "EMRATIO": ("EMRATIO", "M"),
}
ECOS_SERIES = {
    "KR3Y": ("817Y002", "010200000", "D"),
    "KR10Y": ("817Y002", "010210000", "D"),
}
DERIVED_SERIES = {
    "KR10Y3Y": ("KR10Y", "KR3Y", "D"),
}

# KRX Data Marketplace > 기본통계 > 지수 > 주가지수 > PER/PBR/배당수익률.
# pykrx uses the same first-party endpoint (MDCSTAT00702), KOSPI ticker 1001 =>
# indTpCd=1 / indTpCd2=001. KRX itself is the source; this does not touch Earnings.
KRX_INDEX_FUNDAMENTALS = {
    "KOSPI_PER": ("WT_PER", "D"),
    "KOSPI_PBR": ("WT_STKPRC_NETASST_RTO", "D"),
}
KRX_DATA_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_INDEX_BLD = "dbms/MDC/STAT/standard/MDCSTAT00702"
TABLE = "economic_chart_points"
TARGET_SOURCE_TYPE = "economic_chart"


def _numeric(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {".", "-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fred_rows(series_code: str, source_id: str, frequency: str, start: date, end: date) -> list[dict[str, Any]]:
    observations = fetch_fred_observations(
        source_id,
        require_env("FRED_API_KEY"),
        start=start.isoformat(),
        end=end.isoformat(),
    )
    rows: list[dict[str, Any]] = []
    for item in observations:
        value = _numeric(item.get("value"))
        observed = str(item.get("date") or "")[:10]
        if value is None or len(observed) != 10:
            continue
        rows.append({
            "series_code": series_code,
            "observation_date": observed,
            "value": value,
            "frequency": frequency,
            "source": f"FRED:{source_id}",
        })
    return rows


def _ecos_rows(series_code: str, stat_code: str, item_code: str, frequency: str, start: date, end: date) -> list[dict[str, Any]]:
    key = require_env("ECOS_API_KEY")
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/"
        f"{stat_code}/{frequency}/{start:%Y%m%d}/{end:%Y%m%d}/{item_code}"
    )
    response = request_with_retry(lambda: requests.get(url, timeout=45))
    response.raise_for_status()
    payload = response.json()
    source_rows = ((payload.get("StatisticSearch") or {}).get("row") or [])
    rows: list[dict[str, Any]] = []
    for item in source_rows:
        raw_date = str(item.get("TIME") or "")
        value = _numeric(item.get("DATA_VALUE"))
        if len(raw_date) != 8 or value is None:
            continue
        rows.append({
            "series_code": series_code,
            "observation_date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
            "value": value,
            "frequency": frequency,
            "source": f"ECOS:{stat_code}/{item_code}",
        })
    return rows


def _krx_date(value: object) -> str | None:
    raw = str(value or "").strip().replace("/", "").replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _krx_index_payload(start: date, end: date) -> list[dict[str, Any]]:
    # Match the current KRX/pykrx request contract exactly.  The endpoint accepts
    # <=730-day date ranges; callers chunk longer backfills.
    response = request_with_retry(lambda: requests.post(
        KRX_DATA_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd?locale=ko_KR",
        },
        data={
            "bld": KRX_INDEX_BLD,
            "indTpCd": "1",
            "indTpCd2": "001",  # KOSPI index code 1001
            "strtDd": start.strftime("%Y%m%d"),
            "endDd": end.strftime("%Y%m%d"),
        },
        timeout=45,
    ))
    response.raise_for_status()
    payload = response.json()
    output = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(output, list):
        raise RuntimeError("KRX KOSPI PER/PBR 응답 형식이 올바르지 않습니다.")
    return [row for row in output if isinstance(row, dict)]


def _krx_index_rows(start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    result = {code: [] for code in KRX_INDEX_FUNDAMENTALS}
    cursor = start
    first = True
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=729), end)
        if not first:
            time.sleep(1)
        first = False
        for item in _krx_index_payload(cursor, chunk_end):
            observed = _krx_date(item.get("TRD_DD"))
            if observed is None:
                continue
            for code, (field, frequency) in KRX_INDEX_FUNDAMENTALS.items():
                value = _numeric(item.get(field))
                if value is None:
                    continue
                result[code].append({
                    "series_code": code,
                    "observation_date": observed,
                    "value": value,
                    "frequency": frequency,
                    "source": "KRX_DATA_MARKETPLACE:MDCSTAT00702/KOSPI1001",
                })
        cursor = chunk_end + timedelta(days=1)
    return result


def _existing_dates(db: SupabaseRest, series_code: str, start: date) -> set[str]:
    rows = db.request("GET", TABLE, params={
        "select": "observation_date",
        "series_code": f"eq.{series_code}",
        "observation_date": f"gte.{start.isoformat()}",
        "limit": "10000",
    }) or []
    return {str(row.get("observation_date")) for row in rows if row.get("observation_date")}


def _insert_missing(db: SupabaseRest, rows: list[dict[str, Any]], start: date) -> int:
    if not rows:
        return 0
    code = str(rows[0]["series_code"])
    existing = _existing_dates(db, code, start)
    missing = [row for row in rows if str(row["observation_date"]) not in existing]
    if missing:
        db.upsert(TABLE, missing, conflict="series_code,observation_date")
    return len(missing)


def _read_values(db: SupabaseRest, series_code: str, start: date, end: date) -> dict[str, float]:
    rows = db.request("GET", TABLE, params={
        "select": "observation_date,value",
        "series_code": f"eq.{series_code}",
        "observation_date": f"gte.{start.isoformat()}",
        "and": f"(observation_date.lte.{end.isoformat()})",
        "order": "observation_date.asc",
        "limit": "10000",
    }) or []
    return {str(row["observation_date"]): float(row["value"]) for row in rows}


def _derive_spread(db: SupabaseRest, code: str, left: str, right: str, frequency: str, start: date, end: date) -> int:
    lhs = _read_values(db, left, start, end)
    rhs = _read_values(db, right, start, end)
    rows = [{
        "series_code": code,
        "observation_date": observed,
        "value": round(lhs[observed] - rhs[observed], 6),
        "frequency": frequency,
        "source": f"DERIVED:{left}-{right}",
    } for observed in sorted(lhs.keys() & rhs.keys())]
    return _insert_missing(db, rows, start)


def _latest_value(db: SupabaseRest, series_code: str) -> Decimal | None:
    rows = db.request("GET", TABLE, params={
        "select": "value,observation_date",
        "series_code": f"eq.{series_code}",
        "order": "observation_date.desc",
        "limit": "1",
    }) or []
    if not rows or rows[0].get("value") is None:
        return None
    return parse_decimal(rows[0]["value"])


def _economic_chart_targets(db: SupabaseRest, changed_codes: set[str]) -> list[dict[str, Any]]:
    if not changed_codes:
        return []
    targets = db.request("GET", "targets", params={
        "select": "*", "is_active": "eq.true", "source_type": f"eq.{TARGET_SOURCE_TYPE}",
    }) or []
    return [target for target in targets
            if isinstance(target.get("source_config"), dict)
            and str(target["source_config"].get("series_code") or "") in changed_codes]


def check_collected_series_alerts(db: SupabaseRest, changed_codes: set[str]) -> int:
    targets = _economic_chart_targets(db, changed_codes)
    if not targets:
        return 0
    now_iso = datetime.now(timezone.utc).isoformat()
    latest_by_code: dict[str, Decimal | None] = {}
    alerts: list[CheckResult] = []
    for target in targets:
        code = str(target["source_config"]["series_code"])
        if code not in latest_by_code:
            latest_by_code[code] = _latest_value(db, code)
        current = latest_by_code[code]
        if current is None:
            continue
        previous = parse_decimal(target["last_value"]) if target.get("last_value") not in (None, "") else None
        result = CheckResult(target, previous, current, condition_met(target, previous, current))
        db.request(
            "PATCH", "targets", params={"id": f"eq.{target['id']}"},
            body={"last_value": json_number(current), "last_checked_at": now_iso, "last_error": None},
            prefer="return=minimal",
        )
        if result.should_alert:
            alerts.append(result)
    enqueue_alerts(db, alerts)
    return len(alerts)
