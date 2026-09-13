"""One-time validated replacement of core chart history from 1990/2003."""
from __future__ import annotations

import json
import calendar
import html
import re
import time
from collections import defaultdict
from datetime import date
from typing import Any

import requests

from common import SupabaseRest, fetch_fred_observations, request_with_retry, require_env
from signals.economic_chart_pipeline import ECOS_SERIES, TABLE, _ecos_rows, _fred_rows, _numeric
from sources.inflation_rates import fetch_bea_rates, fetch_bls_rates
from sources.policy_rates import fetch_us_policy_rate_chart_rows
from sources.us_treasury_yields import fetch_treasury_yield_rows


START = date(1990, 1, 1)
KOREA_START = date(2003, 1, 1)
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
KOREA_CODES = ("KR3Y", "KR10Y", "KR10Y3Y", "KR_EXPORT_DAILY_AVG")
TRADEDATA_INDEX = "https://tradedata.go.kr/cts/index.do?menuId=ETS_MNU_00000177"
TRADEDATA_URL = "https://tradedata.go.kr/cts/hmpg/retrieveTrade.do"


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
    meeting_dates.update(_fed_meeting_dates(1990, 1999))

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


def _spread_rows(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    code: str = "US10Y2Y",
) -> list[dict[str, Any]]:
    lhs = {str(row["observation_date"]): float(row["value"]) for row in left}
    rhs = {str(row["observation_date"]): float(row["value"]) for row in right}
    return [{
        "series_code": code,
        "observation_date": observed,
        "value": round(lhs[observed] - rhs[observed], 6),
        "frequency": "D",
        "source": f"DERIVED:{left[0]['series_code']}-{right[0]['series_code']}",
    } for observed in sorted(lhs.keys() & rhs.keys())]


def _period_month(value: object) -> date | None:
    text = str(value or "").strip()
    match = re.search(r"(20\d{2})\D*?(0?[1-9]|1[0-2])(?:\D|$)", text)
    if not match:
        match = re.search(r"(20\d{2})(0[1-9]|1[0-2])", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        return None


def _number(value: object) -> float | None:
    try:
        return float(str(value or "").replace(",", "").strip())
    except ValueError:
        return None


def _fetch_monthly_export_totals(start: date, end: date) -> dict[date, float]:
    """Fetch official KCS monthly export totals from TradeData in USD millions."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 MacroWatch/1.0",
        "Referer": "https://tradedata.go.kr/cts/index.do",
        "X-Requested-With": "XMLHttpRequest",
    })
    request_with_retry(lambda: session.get(TRADEDATA_INDEX, timeout=30)).raise_for_status()
    totals: dict[date, float] = {}
    for year in range(start.year, end.year + 1):
        response = request_with_retry(lambda year=year: session.post(TRADEDATA_URL, data={
            "tradeKind": "ETS_MNK_1020000A", "priodKind": "MON",
            "priodFr": f"{year}01", "priodTo": f"{year}12", "statsBase": "acptDd",
            "ttwgTpcd": "1000", "selectPaging": "1", "showPagingLine": "5000",
            "sortColumn": "", "sortOrder": "", "hsSgnGrpCol": "HS2_SGN",
            "hsSgnWhrCol": "HS2_SGN", "hsSgn": "",
        }, timeout=45))
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise RuntimeError(f"TradeData returned no monthly exports for {year}")
        explicit: dict[date, float] = {}
        summed: dict[date, float] = defaultdict(float)
        for item in items:
            if not isinstance(item, dict):
                continue
            month = _period_month(item.get("priodTitle") or item.get("priod"))
            amount_kusd = _number(item.get("expUsdAmt"))
            if month is None or amount_kusd is None or amount_kusd < 0:
                continue
            if str(item.get("hsSgn") or "").strip() in {"총계", "TOTAL", "Total", "합계"}:
                explicit[month] = amount_kusd / 1000.0
            else:
                summed[month] += amount_kusd / 1000.0
        year_values = explicit or dict(summed)
        if len(year_values) < 12 and year < end.year:
            raise RuntimeError(f"TradeData monthly exports incomplete for {year}: {len(year_values)}")
        totals.update({month: value for month, value in year_values.items() if start <= month <= end})
        time.sleep(0.2)
    return totals


def _month_range(start: date, end: date):
    current = start.replace(day=1)
    while current <= end:
        yield current
        current = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)


def _computed_workdays(month: date, kr_holidays: Any) -> float:
    total = 0.0
    for day in range(1, calendar.monthrange(month.year, month.month)[1] + 1):
        observed = date(month.year, month.month, day)
        if observed in kr_holidays or observed.weekday() == 6:
            continue
        total += 0.5 if observed.weekday() == 5 else 1.0
    return total


def _existing_export_rows(db: SupabaseRest, end: date) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = db.request("GET", TABLE, params={
            "select": "series_code,observation_date,value,frequency,source",
            "series_code": "eq.KR_EXPORT_DAILY_AVG",
            "observation_date": f"gte.{KOREA_START.isoformat()}",
            "and": f"(observation_date.lte.{end.isoformat()})",
            "order": "observation_date.asc",
            "limit": "1000",
            "offset": str(offset),
        }) or []
        output.extend(dict(row) for row in page)
        if len(page) < 1000:
            return output
        offset += len(page)


def _korea_export_rows(db: SupabaseRest, end: date) -> list[dict[str, Any]]:
    import holidays

    existing = _existing_export_rows(db, end)
    first_existing_month = (
        date.fromisoformat(str(existing[0]["observation_date"])).replace(day=1)
        if existing else end.replace(day=1)
    )
    backfill_end = date.fromordinal(first_existing_month.toordinal() - 1).replace(day=1)
    totals = _fetch_monthly_export_totals(KOREA_START, backfill_end)
    expected = list(_month_range(KOREA_START, backfill_end))
    missing = [month for month in expected if month not in totals]
    if missing:
        raise RuntimeError(f"TradeData monthly export history is incomplete: {missing[:12]}")
    kr_holidays = holidays.country_holidays("KR", years=range(KOREA_START.year, backfill_end.year + 1))
    rows: list[dict[str, Any]] = []
    for month in expected:
        workdays = _computed_workdays(month, kr_holidays)
        if workdays <= 0:
            raise RuntimeError(f"Invalid Korean customs workdays for {month:%Y-%m}")
        month_end = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
        rows.append({
            "series_code": "KR_EXPORT_DAILY_AVG",
            "observation_date": month_end.isoformat(),
            "value": round(totals[month] / workdays / 100.0, 6),
            "frequency": "M",
            "source": "KCS:TRADEDATA_MONTHLY_EXPORT/computed_workdays",
        })
    # This run expands the accepted series backward. Existing 2016+ monthly and current
    # intra-month observations remain byte-for-byte unchanged.
    rows.extend(existing)
    return sorted(rows, key=lambda row: str(row["observation_date"]))


def _validate(all_rows: dict[str, list[dict[str, Any]]], end: date) -> None:
    expected = {
        "US2Y", "US10Y", "US10Y2Y", "US_POLICY_RATE_MID", "NFCI_CREDIT",
        "USDKRW", "EMRATIO", *INFLATION_CODES, "DRALACBS", *KOREA_CODES,
    }
    if set(all_rows) != expected:
        raise RuntimeError(f"Backfill series mismatch: {sorted(set(all_rows) ^ expected)}")
    for code, rows in all_rows.items():
        if not rows:
            raise RuntimeError(f"Backfill source returned no rows: {code}")
        dates = [str(row["observation_date"]) for row in rows]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise RuntimeError(f"Backfill dates are not unique and ordered: {code}")
        required_start = "2003-03-31" if code in KOREA_CODES else "1990-03-31"
        if dates[0] > required_start:
            raise RuntimeError(f"Backfill does not reach required history: {code} starts {dates[0]}")
        if date.fromisoformat(dates[-1]) > end:
            raise RuntimeError(f"Backfill contains a future row: {code} {dates[-1]}")
    policy_dates = [str(row["observation_date"]) for row in all_rows["US_POLICY_RATE_MID"]]
    if any(a == b for a, b in zip(policy_dates, policy_dates[1:])):
        raise RuntimeError("Policy-rate backfill contains duplicate event dates")


def _existing_dates(db: SupabaseRest, code: str, start: date, end: date) -> set[str]:
    output: set[str] = set()
    offset = 0
    while True:
        page = db.request("GET", TABLE, params={
            "select": "observation_date",
            "series_code": f"eq.{code}",
            "observation_date": f"gte.{start.isoformat()}",
            "and": f"(observation_date.lte.{end.isoformat()})",
            "order": "observation_date.asc",
            "limit": "1000",
            "offset": str(offset),
        }) or []
        output.update(str(row["observation_date"]) for row in page if row.get("observation_date"))
        if len(page) < 1000:
            return output
        offset += len(page)


def _replace(db: SupabaseRest, code: str, rows: list[dict[str, Any]], start: date, end: date) -> dict[str, int]:
    for index in range(0, len(rows), 500):
        db.upsert(TABLE, rows[index:index + 500], conflict="series_code,observation_date")
    expected = {str(row["observation_date"]) for row in rows}
    stale = sorted(_existing_dates(db, code, start, end) - expected)
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
    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        all_rows[code] = _ecos_rows(code, stat_code, item_code, frequency, KOREA_START, end)
    all_rows["KR10Y3Y"] = _spread_rows(all_rows["KR10Y"], all_rows["KR3Y"], "KR10Y3Y")
    all_rows["KR_EXPORT_DAILY_AVG"] = _korea_export_rows(database, end)
    _validate(all_rows, end)

    result = {
        code: _replace(database, code, rows, KOREA_START if code in KOREA_CODES else START, end)
        for code, rows in all_rows.items()
    }
    print(json.dumps({
        "mode": "one-time-backfill",
        "start": {"default": START.isoformat(), "korea": KOREA_START.isoformat()},
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
