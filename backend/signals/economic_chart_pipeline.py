"""Economic chart collector with isolated automatic, bootstrap, and alert responsibilities."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import requests

from common import SupabaseRest, fetch_fred_observations, request_with_retry, require_env
from tracking.check_targets import CheckResult, condition_met, enqueue_alerts, json_number, parse_decimal


FRED_SERIES = {
    "US2Y": ("DGS2", "D"),
    "US10Y": ("DGS10", "D"),
    "HY_OAS": ("BAMLH0A0HYM2", "D"),
    "EM_OAS": ("BAMLEMCBPIOAS", "D"),
    "WTI": ("DCOILWTICO", "D"),
    "USDKRW": ("DEXKOUS", "D"),
    "WEI": ("WEI", "W"),
}
ECOS_SERIES = {
    "KR3Y": ("817Y002", "010200000", "D"),
    "KR10Y": ("817Y002", "010210000", "D"),
}
DERIVED_SERIES = {
    "US10Y2Y": ("US10Y", "US2Y", "D"),
    "KR10Y3Y": ("KR10Y", "KR3Y", "D"),
}
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
    start_text = start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/"
        f"{stat_code}/{frequency}/{start_text}/{end_text}/{item_code}"
    )
    response = request_with_retry(lambda: requests.get(url, timeout=45))
    response.raise_for_status()
    payload = response.json()
    source_rows = ((payload.get("StatisticSearch") or {}).get("row") or [])
    rows: list[dict[str, Any]] = []
    for item in source_rows:
        raw_date = str(item.get("TIME") or "")
        if len(raw_date) != 8:
            continue
        value = _numeric(item.get("DATA_VALUE"))
        if value is None:
            continue
        observed = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        rows.append({
            "series_code": series_code,
            "observation_date": observed,
            "value": value,
            "frequency": frequency,
            "source": f"ECOS:{stat_code}/{item_code}",
        })
    return rows


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
    rows = [
        {
            "series_code": code,
            "observation_date": observed,
            "value": round(lhs[observed] - rhs[observed], 6),
            "frequency": frequency,
            "source": f"DERIVED:{left}-{right}",
        }
        for observed in sorted(lhs.keys() & rhs.keys())
    ]
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
        "select": "*",
        "is_active": "eq.true",
        "source_type": f"eq.{TARGET_SOURCE_TYPE}",
    }) or []
    return [
        target for target in targets
        if isinstance(target.get("source_config"), dict)
        and str(target["source_config"].get("series_code") or "") in changed_codes
    ]


def check_collected_series_alerts(db: SupabaseRest, changed_codes: set[str]) -> int:
    """Evaluate only chart targets whose series received a new automatic observation."""
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


def collect(mode: str) -> dict[str, int]:
    today = date.today()
    start = today - (timedelta(days=3660) if mode == "bootstrap" else timedelta(days=45))
    db = SupabaseRest()
    counts: dict[str, int] = {}

    for code, (source_id, frequency) in FRED_SERIES.items():
        counts[code] = _insert_missing(db, _fred_rows(code, source_id, frequency, start, today), start)

    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        counts[code] = _insert_missing(db, _ecos_rows(code, stat_code, item_code, frequency, start, today), start)

    for code, (left, right, frequency) in DERIVED_SERIES.items():
        counts[code] = _derive_spread(db, code, left, right, frequency, start, today)

    alert_count = 0
    if mode == "automatic":
        changed_codes = {code for code, inserted in counts.items() if inserted > 0}
        alert_count = check_collected_series_alerts(db, changed_codes)

    print({"mode": mode, "start": start.isoformat(), "end": today.isoformat(), "inserted": counts, "alerts": alert_count})
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("automatic", "bootstrap"), default="automatic")
    args = parser.parse_args()
    collect(args.mode)


if __name__ == "__main__":
    main()
