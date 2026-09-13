"""Collect and store the weekly stock-attractiveness flow."""

from __future__ import annotations

import re
import argparse
from datetime import date, datetime, timedelta, timezone

import requests

from common import AUTOMATIC_WEEKLY_WEEKS, SupabaseRest, fetch_fred_observations, require_env, request_with_retry
from signals.canonical_series import load_many, rows as canonical_rows, store as store_canonical
from signals.equity_bond_attractiveness import METHOD_VERSION, QuarterlyInput, build_weekly_rows
from sources.market import fetch_yahoo_adjusted, valid_fred_values


OEF_PAGE = "https://www.ishares.com/us/products/239723/ishares-sp-100-etf"
KOREA_10Y_STAT = "817Y002"
KOREA_10Y_ITEM = "010210000"
# The explicit one-time cache initialization retains enough history for exact
# 260-week percentile parity, the 13/52-week dependencies, and smoothing.
INITIALIZATION_HISTORY_WEEKS = 340
CANONICAL_CODES = {
    "KR_EQUITY": "KOSPI_CLOSE", "KR_YIELD": "KR10Y",
    "US_EQUITY": "OEF_ADJUSTED_CLOSE", "US_YIELD": "US10Y",
}


def weekly_last(values: dict[date, float]) -> dict[date, float]:
    result: dict[date, tuple[date, float]] = {}
    for observed, value in sorted(values.items()):
        week = observed + timedelta(days=4 - observed.weekday())
        if week <= date.today() and (week not in result or observed > result[week][0]):
            result[week] = (observed, value)
    return {week: item[1] for week, item in result.items()}


def align_to_weeks(values: dict[date, float], weeks: list[date]) -> dict[date, float]:
    dates = sorted(values)
    result: dict[date, float] = {}
    position = 0
    latest = None
    for week in weeks:
        while position < len(dates) and dates[position] <= week:
            latest = values[dates[position]]
            position += 1
        if latest is not None:
            result[week] = latest
    return result


def fetch_ecos_10y(api_key: str, start: date, end: date) -> dict[date, float]:
    values: dict[date, float] = {}
    session = requests.Session()
    for year in range(start.year, end.year + 1):
        first = max(start, date(year, 1, 1)).strftime("%Y%m%d")
        last = min(end, date(year, 12, 31)).strftime("%Y%m%d")
        url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/"
               f"{KOREA_10Y_STAT}/D/{first}/{last}/{KOREA_10Y_ITEM}")
        response = request_with_retry(lambda: session.get(url, timeout=45))
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("StatisticSearch", {}).get("row", [])
        if not rows and payload.get("RESULT", {}).get("CODE") not in (None, "INFO-200"):
            raise RuntimeError(f"ECOS rejected Korea 10Y request: {payload['RESULT'].get('CODE')}")
        for row in rows:
            try:
                values[datetime.strptime(row["TIME"], "%Y%m%d").date()] = float(row["DATA_VALUE"])
            except (KeyError, TypeError, ValueError):
                continue
    if not values:
        raise RuntimeError("ECOS returned no Korea 10Y observations")
    return values


def fetch_oef_pe() -> float:
    response = request_with_retry(lambda: requests.get(
        OEF_PAGE,
        headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
        timeout=45,
    ))
    response.raise_for_status()
    match = re.search(r"P/E Ratio.{0,500}?([0-9]{1,3}\.[0-9]{1,2})", response.text, re.I | re.S)
    if not match:
        raise RuntimeError("iShares OEF page no longer exposes the expected P/E ratio")
    value = float(match.group(1))
    if not 1 < value < 200:
        raise RuntimeError("iShares OEF P/E ratio is outside the validation range")
    return value


def load_quarters(database: SupabaseRest) -> dict[str, list[QuarterlyInput]]:
    rows = database.request("POST", "rpc/equity_bond_attractiveness_inputs", body={}) or []
    grouped = {"KR": [], "US": []}
    for row in rows:
        grouped[row["country"]].append(QuarterlyInput(
            period_end=date.fromisoformat(row["reference_date"]),
            net_income=float(row["net_income_total"]),
            market_cap=float(row["market_cap_total"]) if row.get("market_cap_total") is not None else None,
        ))
    return grouped


def stored_rows(country: str, rows: list[dict], calculated_at: str) -> list[dict]:
    return [{
        "country": country,
        "observation_date": row["observation_date"].isoformat(),
        "score": round(row["score"], 6),
        "earnings_yield_pct": round(row["earnings_yield_pct"], 6),
        "sovereign_yield_pct": round(row["sovereign_yield_pct"], 6),
        "yield_gap_pct": round(row["yield_gap_pct"], 6),
        "earnings_momentum_pct": round(row["earnings_momentum_pct"], 6),
        "equity_return_13w_pct": round(row["equity_return_13w_pct"], 6),
        "component_scores": row["components"],
        "method_version": METHOD_VERSION,
        "calculated_at": calculated_at,
    } for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize-sources", action="store_true")
    args = parser.parse_args()
    today = date.today()
    database = SupabaseRest()
    existing = database.request(
        "GET", "equity_bond_attractiveness_weekly",
        params={
            "select": (
                "country,observation_date,method_version,score,earnings_yield_pct,"
                "sovereign_yield_pct,yield_gap_pct,earnings_momentum_pct,"
                "equity_return_13w_pct,component_scores"
            ),
            "limit": "10000",
        },
    ) or []
    quarters = load_quarters(database)
    source_start = today - timedelta(weeks=INITIALIZATION_HISTORY_WEEKS) if args.initialize_sources else today - timedelta(weeks=AUTOMATIC_WEEKLY_WEEKS)
    yahoo_daily = {symbol: fetch_yahoo_adjusted(symbol, source_start, today) for symbol in ("^KS11", "OEF")}
    fred_daily = valid_fred_values(fetch_fred_observations(
        "DGS10", require_env("FRED_API_KEY"), start=source_start.isoformat(), end=today.isoformat()
    ))
    ecos_daily = fetch_ecos_10y(require_env("ECOS_API_KEY"), source_start, today)
    recent = {
        "KR_EQUITY": yahoo_daily["^KS11"], "KR_YIELD": ecos_daily,
        "US_EQUITY": yahoo_daily["OEF"], "US_YIELD": fred_daily,
    }
    if args.initialize_sources and any(len(weekly_last(recent.get(series, {}))) < 260 for series in CANONICAL_CODES):
        raise RuntimeError("Attractiveness source initialization returned insufficient history")
    sources = {
        "KR_EQUITY": ("D", "YAHOO:^KS11"), "KR_YIELD": ("D", "ECOS:817Y002/010210000"),
        "US_EQUITY": ("D", "YAHOO:OEF"), "US_YIELD": ("D", "USTREASURY:10Y"),
    }
    payload = []
    for series, values in recent.items():
        frequency, source = sources[series]
        payload.extend(canonical_rows(CANONICAL_CODES[series], values, frequency=frequency, source=source))
    store_canonical(database, payload)
    canonical = load_many(database, CANONICAL_CODES.values())
    weekly = {series: weekly_last(canonical[code]) for series, code in CANONICAL_CODES.items()}
    if any(len(weekly.get(series, {})) < 260 for series in CANONICAL_CODES):
        raise RuntimeError("Attractiveness canonical source history is incomplete; run --initialize-sources explicitly")
    yahoo = {
        "^KS11": weekly["KR_EQUITY"],
        "OEF": weekly["US_EQUITY"],
    }
    fred = weekly["US_YIELD"]
    ecos = weekly["KR_YIELD"]
    calculated_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for country, equity_symbol, yields, anchor in (
        ("KR", "^KS11", ecos, None),
        ("US", "OEF", fred, 100.0 / fetch_oef_pe()),
    ):
        weeks = sorted(yahoo[equity_symbol])
        result = build_weekly_rows(
            country,
            weeks,
            yahoo[equity_symbol],
            align_to_weeks(yields, weeks),
            quarters[country],
            us_anchor_earnings_yield=anchor,
        )
        rows.extend(stored_rows(country, result, calculated_at))
    if not rows:
        raise RuntimeError("No attractiveness rows were calculated")
    existing_by_key = {
        (str(row["country"]), str(row["observation_date"]), str(row["method_version"])): row
        for row in existing
    }
    compare_fields = (
        "score", "earnings_yield_pct", "sovereign_yield_pct", "yield_gap_pct",
        "earnings_momentum_pct", "equity_return_13w_pct", "component_scores",
    )
    refresh_cutoff = today - timedelta(weeks=AUTOMATIC_WEEKLY_WEEKS)
    writable = [
        row for row in rows
        if (
            (key := (str(row["country"]), str(row["observation_date"]), str(row["method_version"])))
            not in existing_by_key
            or (
                date.fromisoformat(str(row["observation_date"])[:10]) >= refresh_cutoff
                and any(existing_by_key[key].get(field) != row.get(field) for field in compare_fields)
            )
        )
    ]
    if writable:
        database.upsert("equity_bond_attractiveness_weekly", writable, conflict="country,observation_date,method_version")
    print(
        f"source_start={source_start.isoformat()} initialized={args.initialize_sources} calculated={len(rows)} stored={len(writable)} "
        f"kr={sum(row['country']=='KR' for row in writable)} us={sum(row['country']=='US' for row in writable)}"
    )


if __name__ == "__main__":
    main()
