"""One-time validated replacement of the common U.S. chart history from 1995."""
from __future__ import annotations

import json
import calendar
import html
import re
from datetime import date
from typing import Any

import requests

from common import SupabaseRest, fetch_fred_observations, request_with_retry, require_env
from signals.economic_chart_pipeline import TABLE, _fred_rows, _numeric
from sources.inflation_rates import fetch_bea_rates, fetch_bls_rates
from sources.policy_rates import fetch_us_policy_rate_chart_rows
from sources.us_treasury_yields import fetch_treasury_yield_rows


START = date(1995, 1, 1)
# The target became a range at the 2008-12-16 decision. Earlier FRED values are
# collapsed to target-rate changes; stored range decisions are represented by their midpoint.
POLICY_MIDPOINT_START = date(2008, 12, 16)
EXTERNAL_FRED = {
    "NFCI_CREDIT": ("NFCICREDIT", "W"),
    "USDKRW": ("DEXKOUS", "D"),
    "EMRATIO": ("EMRATIO", "M"),
    "DRALACBS": ("DRALACBS", "Q"),
}
INFLATION_CODES = ("US_CPI", "US_CORE_CPI", "US_PCE", "US_CORE_PCE")


def _policy_rows(db: SupabaseRest, end: date) -> list[dict[str, Any]]:
    observations = fetch_fred_observations(
        "DFEDTAR", require_env("FRED_API_KEY"),
        start=START.isoformat(), end="2008-12-15",
    )
    values: dict[date, float] = {}
    change_dates: set[date] = set()
    previous: float | None = None
    for item in observations:
        value = _numeric(item.get("value"))
        observed = str(item.get("date") or "")[:10]
        if value is None or len(observed) != 10:
            continue
        observed_date = date.fromisoformat(observed)
        values[observed_date] = value
        if previous is not None and value != previous:
            change_dates.add(observed_date)
        previous = value

    # Preserve unchanged-rate decisions too. The existing event store is authoritative from
    # 2000; official Fed historical-year pages supply the earlier meeting dates.
    stored_dates = db.request("GET", "central_bank_policy_events", params={
        "select": "meeting_date",
        "central_bank": "eq.fed",
        "analysis_status": "eq.completed",
        "meeting_date": "gte.2000-01-01",
        "and": "(meeting_date.lte.2008-12-15)",
        "order": "meeting_date.asc",
        "limit": "1000",
    }) or []
    meeting_dates = {
        date.fromisoformat(str(row["meeting_date"]))
        for row in stored_dates if row.get("meeting_date")
    }
    meeting_dates.update(_fed_meeting_dates(1995, 1999))

    historical: list[dict[str, Any]] = []
    available_dates = sorted(values)
    for observed in sorted(meeting_dates | change_dates):
        value = values.get(observed)
        if value is None:
            following = next((day for day in available_dates if observed < day <= date.fromordinal(observed.toordinal() + 3)), None)
            value = values.get(following) if following else None
        if value is None:
            raise RuntimeError(f"No DFEDTAR value found for policy event {observed.isoformat()}")
        historical.append({
            "series_code": "US_POLICY_RATE_MID",
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "E",
            "source": "FED:meeting-date/FRED:DFEDTAR",
        })
    midpoints = fetch_us_policy_rate_chart_rows(db, POLICY_MIDPOINT_START, end)
    return historical + midpoints


def _fed_meeting_dates(first_year: int, last_year: int) -> set[date]:
    months = {name: index for index, name in enumerate(calendar.month_name) if name}
    output: set[date] = set()
    heading = re.compile(
        r"<h5>\s*([A-Z][a-z]+)\s+(\d{1,2})(?:-([A-Z][a-z]+)?\s*(\d{1,2}))?\s+Meeting\s+-\s+(\d{4})\s*</h5>",
        re.IGNORECASE,
    )
    for year in range(first_year, last_year + 1):
        url = f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
        response = request_with_retry(lambda url=url: requests.get(url, timeout=45))
        response.raise_for_status()
        for match in heading.finditer(html.unescape(response.text)):
            month1, day1, month2, day2, raw_year = match.groups()
            month = months[(month2 or month1).title()]
            day = int(day2 or day1)
            output.add(date(int(raw_year), month, day))
    if len(output) < (last_year - first_year + 1) * 7:
        raise RuntimeError(f"Federal Reserve historical pages returned too few meetings: {len(output)}")
    return output


def _spread_rows(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lhs = {str(row["observation_date"]): float(row["value"]) for row in left}
    rhs = {str(row["observation_date"]): float(row["value"]) for row in right}
    return [{
        "series_code": "US10Y2Y",
        "observation_date": observed,
        "value": round(lhs[observed] - rhs[observed], 6),
        "frequency": "D",
        "source": "DERIVED:US10Y-US2Y",
    } for observed in sorted(lhs.keys() & rhs.keys())]


def _validate(all_rows: dict[str, list[dict[str, Any]]], end: date) -> None:
    expected = {
        "US2Y", "US10Y", "US10Y2Y", "US_POLICY_RATE_MID", "NFCI_CREDIT",
        "USDKRW", "EMRATIO", *INFLATION_CODES, "DRALACBS",
    }
    if set(all_rows) != expected:
        raise RuntimeError(f"Backfill series mismatch: {sorted(set(all_rows) ^ expected)}")
    for code, rows in all_rows.items():
        if not rows:
            raise RuntimeError(f"Backfill source returned no rows: {code}")
        dates = [str(row["observation_date"]) for row in rows]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise RuntimeError(f"Backfill dates are not unique and ordered: {code}")
        if dates[0] > "1995-03-31":
            raise RuntimeError(f"Backfill does not reach early 1995: {code} starts {dates[0]}")
        if date.fromisoformat(dates[-1]) > end:
            raise RuntimeError(f"Backfill contains a future row: {code} {dates[-1]}")
    policy_dates = [str(row["observation_date"]) for row in all_rows["US_POLICY_RATE_MID"]]
    if any(a == b for a, b in zip(policy_dates, policy_dates[1:])):
        raise RuntimeError("Policy-rate backfill contains duplicate event dates")


def _existing_dates(db: SupabaseRest, code: str, end: date) -> set[str]:
    output: set[str] = set()
    offset = 0
    while True:
        page = db.request("GET", TABLE, params={
            "select": "observation_date",
            "series_code": f"eq.{code}",
            "observation_date": f"gte.{START.isoformat()}",
            "and": f"(observation_date.lte.{end.isoformat()})",
            "order": "observation_date.asc",
            "limit": "1000",
            "offset": str(offset),
        }) or []
        output.update(str(row["observation_date"]) for row in page if row.get("observation_date"))
        if len(page) < 1000:
            return output
        offset += len(page)


def _replace(db: SupabaseRest, code: str, rows: list[dict[str, Any]], end: date) -> dict[str, int]:
    for index in range(0, len(rows), 500):
        db.upsert(TABLE, rows[index:index + 500], conflict="series_code,observation_date")
    expected = {str(row["observation_date"]) for row in rows}
    stale = sorted(_existing_dates(db, code, end) - expected)
    for observed in stale:
        db.request("DELETE", TABLE, params={
            "series_code": f"eq.{code}",
            "observation_date": f"eq.{observed}",
        }, prefer="return=minimal")
    return {"rows": len(rows), "removed": len(stale)}


def backfill(today: date | None = None, db: SupabaseRest | None = None) -> dict[str, dict[str, int]]:
    end = today or date.today()
    database = db or SupabaseRest()

    # Finish every external/database read and validate the complete replacement set first.
    treasury = fetch_treasury_yield_rows(START, end)
    all_rows: dict[str, list[dict[str, Any]]] = {
        "US2Y": treasury["US2Y"],
        "US10Y": treasury["US10Y"],
    }
    all_rows["US10Y2Y"] = _spread_rows(all_rows["US10Y"], all_rows["US2Y"])
    for code, (source_id, frequency) in EXTERNAL_FRED.items():
        all_rows[code] = _fred_rows(code, source_id, frequency, START, end)
    all_rows["US_POLICY_RATE_MID"] = _policy_rows(database, end)
    inflation = {**fetch_bls_rates(START, end), **fetch_bea_rates(START, end)}
    all_rows.update({code: inflation[code] for code in INFLATION_CODES})
    _validate(all_rows, end)

    result = {code: _replace(database, code, rows, end) for code, rows in all_rows.items()}
    print(json.dumps({
        "mode": "one-time-backfill",
        "start": START.isoformat(),
        "end": end.isoformat(),
        "series": result,
        "bounds": {
            code: [rows[0]["observation_date"], rows[-1]["observation_date"]]
            for code, rows in all_rows.items()
        },
    }, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    backfill()


if __name__ == "__main__":
    main()
