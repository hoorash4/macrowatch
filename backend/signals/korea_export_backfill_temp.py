"""Temporary one-off Korea export backfill. Delete after verified run."""
from __future__ import annotations

import calendar
import json
import re
import time
from collections import defaultdict
from datetime import date

import holidays
import requests

from common import SupabaseRest
from sources.korea_export_intramonth import (
    USER_AGENT,
    ExportSnapshot,
    _get_detail,
    _reported_daily_average,
    _workdays,
    _clean,
    fetch_release_links,
    independent_segment_rows,
    parse_snapshot,
)
from sources.korea_export_monthly import monthly_row_from_snapshot

SERIES = "KR_EXPORT_DAILY_AVG"
START = date(2016, 1, 1)
RAW_TABLE = "korea_export_intramonth_snapshots"
TRADEDATA_INDEX = "https://tradedata.go.kr/cts/index.do?menuId=ETS_MNU_00000177"
TRADEDATA_URL = "https://tradedata.go.kr/cts/hmpg/retrieveTrade.do"


def _fallback_snapshot(markup: str, link) -> ExportSnapshot:
    """Recover legacy KCS pages whose old HTML table layout defeats the modern parser."""
    text = _clean(markup)
    workdays = _workdays(text)
    daily_avg = _reported_daily_average(text)
    if workdays is None or workdays <= 0 or daily_avg is None or daily_avg <= 0:
        raise ValueError("legacy KCS daily average/workdays not recoverable")
    source_url = f"https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do?bbsId=1362&mi=2891&nttSn={link.ntt_sn}"
    if link.ntt_url:
        source_url += f"&nttSnUrl={link.ntt_url}"
    return ExportSnapshot(
        stage=link.stage,
        reference_month=link.reference_month,
        period_end=link.period_end,
        cumulative_export_musd=round(daily_avg * workdays * 100.0, 6),
        cumulative_workdays=workdays,
        published_on=link.published_on,
        source_url=source_url,
    )


def _fetch_all_snapshots() -> tuple[list[ExportSnapshot], list[str], int]:
    links, errors = fetch_release_links(START, max_pages=80)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    snapshots: list[ExportSnapshot] = []
    recovered = 0
    for index, link in enumerate(links):
        try:
            markup = _get_detail(session, link)
            try:
                snapshot = parse_snapshot(markup, link)
            except Exception as primary_error:
                try:
                    snapshot = _fallback_snapshot(markup, link)
                    recovered += 1
                except Exception as fallback_error:
                    errors.append(
                        f"{link.title}: {primary_error.__class__.__name__}: {primary_error}; "
                        f"fallback={fallback_error.__class__.__name__}: {fallback_error}"
                    )
                    continue
            snapshots.append(snapshot)
        except Exception as error:
            errors.append(f"{link.title}: {error.__class__.__name__}: {error}")
        if index + 1 < len(links):
            time.sleep(0.12)
    return snapshots, errors, recovered


def _period_month(value: object) -> date | None:
    text = str(value or "").strip()
    match = re.search(r"(20\d{2})\D*?(0?[1-9]|1[0-2])(?:\D|$)", text)
    if not match:
        compact = re.search(r"(20\d{2})(0[1-9]|1[0-2])", text)
        match = compact
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        return None


def _number(value: object) -> float | None:
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _fetch_monthly_export_totals(start: date, end: date) -> tuple[dict[date, float], list[str]]:
    """Fetch official KCS monthly exports from TradeData; values returned in USD million."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": "https://tradedata.go.kr/cts/index.do",
        "X-Requested-With": "XMLHttpRequest",
    })
    errors: list[str] = []
    try:
        session.get(TRADEDATA_INDEX, timeout=30).raise_for_status()
    except Exception as error:
        errors.append(f"TradeData session init: {error.__class__.__name__}: {error}")

    totals: dict[date, float] = {}
    for year in range(start.year, end.year + 1):
        params = {
            "tradeKind": "ETS_MNK_1020000A",
            "priodKind": "MON",
            "priodFr": f"{year}01",
            "priodTo": f"{year}12",
            "statsBase": "acptDd",
            "ttwgTpcd": "1000",
            "selectPaging": "1",
            "showPagingLine": "5000",
            "sortColumn": "",
            "sortOrder": "",
            "hsSgnGrpCol": "HS2_SGN",
            "hsSgnWhrCol": "HS2_SGN",
            "hsSgn": "",
        }
        try:
            response = session.post(TRADEDATA_URL, data=params, timeout=45)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError("TradeData returned no items")
            explicit: dict[date, float] = {}
            summed: dict[date, float] = defaultdict(float)
            for item in items:
                if not isinstance(item, dict):
                    continue
                month = _period_month(item.get("priodTitle") or item.get("priod"))
                amount_kusd = _number(item.get("expUsdAmt"))
                if month is None or amount_kusd is None or amount_kusd < 0:
                    continue
                hs = str(item.get("hsSgn") or "").strip()
                if hs in {"총계", "TOTAL", "Total", "합계"}:
                    explicit[month] = amount_kusd / 1000.0
                else:
                    summed[month] += amount_kusd / 1000.0
            year_values = explicit or dict(summed)
            for month, amount_musd in year_values.items():
                if start <= month <= end and amount_musd > 0:
                    totals[month] = amount_musd
            if not year_values:
                errors.append(f"TradeData {year}: no usable monthly totals; keys={list(items[0].keys())[:20]}")
        except Exception as error:
            errors.append(f"TradeData {year}: {error.__class__.__name__}: {error}")
        time.sleep(0.2)
    return totals, errors


def _computed_workdays(month: date, kr_holidays) -> float:
    total = 0.0
    last = calendar.monthrange(month.year, month.month)[1]
    for day in range(1, last + 1):
        observed = date(month.year, month.month, day)
        if observed in kr_holidays or observed.weekday() == 6:
            continue
        if observed.weekday() == 5:
            total += 0.5
        else:
            total += 1.0
    return total


def _month_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def main() -> None:
    today = date.today()
    current_month = today.replace(day=1)
    last_complete = date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)
    snapshots, errors, recovered = _fetch_all_snapshots()
    if not snapshots:
        raise RuntimeError(f"No KCS snapshots fetched; errors={errors[:10]}")

    by_month = defaultdict(list)
    for snapshot in snapshots:
        by_month[snapshot.reference_month].append(snapshot)

    segment_rows = independent_segment_rows(snapshots)
    segment_months = {date.fromisoformat(row["observation_date"]).replace(day=1) for row in segment_rows}

    monthly_totals, monthly_errors = _fetch_monthly_export_totals(START, last_complete)
    errors.extend(monthly_errors)
    kr_holidays = holidays.country_holidays("KR", years=range(START.year, today.year + 1))

    monthly_rows = []
    computed_workday_rows = 0
    official_workday_rows = 0
    for month in _month_range(START, last_complete):
        if month in segment_months:
            continue
        items = by_month.get(month, [])
        month_end = next((item for item in items if item.stage == "month_end"), None)
        if month_end is not None:
            monthly_rows.append(monthly_row_from_snapshot(month_end))
            official_workday_rows += 1
            continue
        amount_musd = monthly_totals.get(month)
        if amount_musd is None:
            continue
        workdays = _computed_workdays(month, kr_holidays)
        if workdays <= 0:
            continue
        last_day = calendar.monthrange(month.year, month.month)[1]
        monthly_rows.append({
            "series_code": SERIES,
            "observation_date": date(month.year, month.month, last_day).isoformat(),
            "value": round(amount_musd / workdays / 100.0, 6),
            "frequency": "M",
            "source": "KCS:TRADEDATA_MONTHLY_EXPORT/computed_workdays",
        })
        computed_workday_rows += 1

    # Keep the current/incomplete month exclusively on actual intra-month observations.
    segment_rows = [r for r in segment_rows if date.fromisoformat(r["observation_date"]).replace(day=1) <= current_month]
    rows = sorted(segment_rows + monthly_rows, key=lambda row: row["observation_date"])
    if not rows:
        raise RuntimeError("KCS backfill produced no rows")

    db = SupabaseRest()
    # Build first; only after a valid replacement exists do we replace the sparse old series/raw cache.
    db.request("DELETE", "economic_chart_points", params={"series_code": f"eq.{SERIES}"}, prefer="return=minimal")
    db.request("DELETE", RAW_TABLE, params={"reference_month": f"gte.{START.isoformat()}"}, prefer="return=minimal")

    raw_rows = [{
        "reference_month": s.reference_month.isoformat(),
        "stage": s.stage,
        "period_end": s.period_end.isoformat(),
        "cumulative_export_musd": s.cumulative_export_musd,
        "cumulative_workdays": s.cumulative_workdays,
        "published_on": s.published_on.isoformat() if s.published_on else None,
        "source_url": s.source_url,
    } for s in snapshots]
    if raw_rows:
        db.upsert(RAW_TABLE, raw_rows, conflict="reference_month,stage")
    db.upsert("economic_chart_points", rows, conflict="series_code,observation_date")

    source_counts = defaultdict(int)
    covered_months = set()
    for row in rows:
        source_counts[row["source"]] += 1
        covered_months.add(date.fromisoformat(row["observation_date"]).replace(day=1))
    expected_complete_months = set(_month_range(START, last_complete))
    missing_complete = sorted(expected_complete_months - covered_months)
    print(json.dumps({
        "stage": "korea_export_backfill_temp",
        "start": START.isoformat(),
        "today": today.isoformat(),
        "snapshots": len(snapshots),
        "legacy_snapshots_recovered": recovered,
        "tradedata_months": len(monthly_totals),
        "covered_complete_months": len(expected_complete_months - set(missing_complete)),
        "expected_complete_months": len(expected_complete_months),
        "missing_complete_months": [m.isoformat() for m in missing_complete],
        "rows": len(rows),
        "segment_rows": len(segment_rows),
        "monthly_fallback_rows": len(monthly_rows),
        "official_workday_rows": official_workday_rows,
        "computed_workday_rows": computed_workday_rows,
        "min_date": rows[0]["observation_date"],
        "max_date": rows[-1]["observation_date"],
        "source_counts": dict(source_counts),
        "errors_count": len(errors),
        "errors_sample": errors[:30],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
