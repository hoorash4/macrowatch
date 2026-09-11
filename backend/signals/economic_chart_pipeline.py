"""Economic chart collector: daily/weekly official series with explicit bootstrap mode."""
from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from typing import Any

import requests

from common import SupabaseRest, fetch_fred_observations, request_with_retry, require_env


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

    print({"mode": mode, "start": start.isoformat(), "end": today.isoformat(), "inserted": counts})
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("automatic", "bootstrap"), default="automatic")
    args = parser.parse_args()
    collect(args.mode)


if __name__ == "__main__":
    main()
