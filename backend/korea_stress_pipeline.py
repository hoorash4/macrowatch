"""Build MacroWatch's Korean market-stress index from official BOK data."""

from __future__ import annotations

import argparse
import csv
import io
import os
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests


ECOS = "https://ecos.bok.or.kr/api"
BOK_SNAPSHOT_FSI = "https://snapshot.bok.or.kr/api/chart/getChart?id=1583"
MARKET_RATES = "817Y002"
EXCHANGE_RATES = "731Y001"
KOSPI_TABLE = "802Y001"
SERIES = {
    "treasury_3y": (MARKET_RATES, "010200000"),
    "aa_minus_3y": (MARKET_RATES, "010300000"),
    "bbb_minus_3y": (MARKET_RATES, "010320000"),
    "cd_91d": (MARKET_RATES, "010502000"),
    "cp_91d": (MARKET_RATES, "010503000"),
    "koribor_3m": (MARKET_RATES, "010150000"),
    "kofr": (MARKET_RATES, "010901000"),
    "usdkrw": (EXCHANGE_RATES, "0000001"),
    "kospi_close": (KOSPI_TABLE, "0001000"),
}
# Fixed stress bands, rather than ranges recalculated from the displayed
# period.  This keeps an older quiet period from being re-scaled upward when
# more observations are added later.
STRESS_BANDS = {
    "investment_grade": (0.3, 3.0),      # AA- corporate 3Y - KTB 3Y
    "rating_gap": (1.0, 8.0),            # BBB- corporate 3Y - AA- corporate 3Y
    "short_term_funding": (0.0, 2.0),    # CP 91D - CD 91D
    "interbank_liquidity": (0.0, 1.5),   # KORIBOR 3M - KOFR
}
TIMEOUT = 45
WEEKLY_DISPLAY_START = date(2023, 9, 1)
KMSI_COMPONENT_WEIGHT = 0.30
KMSI_FSI_WEIGHT = 0.70
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Cache-Control": "no-cache",
}


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def request(path: list[str]) -> dict:
    url = "/".join([ECOS, *path])
    last_error: requests.RequestException | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=(12, TIMEOUT))
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"ECOS connection failed after retries: {last_error}")


def ecos_rows(key: str, stat: str, cycle: str, start: str, end: str, item: str = "") -> list[dict]:
    """Fetch every requested ECOS row in its supported 100-row pages."""
    rows: list[dict] = []
    page_start = 1
    while True:
        path = [
            "StatisticSearch", quote(key, safe=""), "json", "kr",
            str(page_start), str(page_start + 99), stat, cycle, start, end,
        ]
        if item:
            path.append(item)
        payload = request(path)
        result = payload.get("StatisticSearch", {})
        page = result.get("row", [])
        if not page:
            break
        rows.extend(page)
        total = int(result.get("list_total_count", len(rows)))
        if len(rows) >= total or len(page) < 100:
            break
        page_start += 100
    return rows


def daily_month_end(key: str, stat: str, item: str, years: int) -> dict[str, float]:
    today = date.today()
    # ECOS becomes unreliable when a multi-year daily range is searched at
    # once.  Keep each official request within one calendar year, then merge
    # the pages locally.
    rows: list[dict] = []
    for year in range(today.year - years, today.year + 1):
        start = f"{year}0101"
        end = today.strftime("%Y%m%d") if year == today.year else f"{year}1231"
        rows.extend(ecos_rows(key, stat, "D", start, end, item))
    values: dict[str, tuple[str, float]] = {}
    for row in rows:
        try:
            observed, value = str(row["TIME"]), float(row["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
        month = f"{observed[:4]}-{observed[4:6]}-01"
        if month not in values or observed > values[month][0]:
            values[month] = (observed, value)
    return {month: value for month, (_observed, value) in values.items()}


def daily_friday_values(key: str, stat: str, item: str, years: int) -> dict[str, tuple[date, float]]:
    """Return each Friday-ending week's last official daily value."""
    today = date.today()
    rows: list[dict] = []
    for year in range(today.year - years, today.year + 1):
        start = f"{year}0101"
        end = today.strftime("%Y%m%d") if year == today.year else f"{year}1231"
        rows.extend(ecos_rows(key, stat, "D", start, end, item))
    closes: dict[str, tuple[date, float]] = {}
    for row in rows:
        try:
            observed = datetime.strptime(str(row["TIME"]), "%Y%m%d").date()
            value = float(row["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
        friday = observed + timedelta(days=4 - observed.weekday())
        # Do not label a partial current week as a completed Friday close.
        if friday > today:
            continue
        week = friday.isoformat()
        if week not in closes or observed > closes[week][0]:
            closes[week] = (observed, value)
    return closes


def fetch_bok_fsi(years: int) -> dict[str, float]:
    """Read the Bank of Korea's published FSI comparison series directly."""
    headers = {**HEADERS, "Referer": "https://snapshot.bok.or.kr/dashboard/A6"}
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(BOK_SNAPSHOT_FSI, headers=headers, timeout=(12, TIMEOUT))
            response.raise_for_status()
            csv_text = response.json()["data"]["chart_opt"]["data"]["csv"]
            first_month = date.today().replace(year=date.today().year - years, day=1).isoformat()
            values: dict[str, float] = {}
            for row in csv.DictReader(io.StringIO(csv_text)):
                try:
                    month = datetime.fromtimestamp(float(row["period"]) / 1000, tz=timezone.utc).date().replace(day=1).isoformat()
                    if month >= first_month:
                        values[month] = float(next(value for key, value in row.items() if key != "period"))
                except (KeyError, StopIteration, TypeError, ValueError, OSError):
                    continue
            if values:
                return values
            raise RuntimeError("BOK FSI 응답에서 유효한 월별 값을 찾지 못했습니다.")
        except (requests.RequestException, KeyError, TypeError, ValueError, RuntimeError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"BOK FSI collection failed after retries: {last_error}")


def score(value: float, low: float, high: float) -> float:
    return max(0.0, (value - low) / (high - low) * 100.0)


def upsert(rows: list[dict], url: str, service_key: str, table: str, conflict: str) -> None:
    response = requests.post(
        f"{url.rstrip('/')}/rest/v1/{table}?on_conflict={conflict}",
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows,
        timeout=TIMEOUT,
    )
    if not response.ok:
        raise RuntimeError(
            f"Supabase Korea stress upsert failed: {response.status_code} "
            f"{response.text[:1200]}"
        )


def fetch_existing_fsi(url: str, service_key: str, years: int) -> dict[str, float]:
    """Keep the last official FSI reading if the source is briefly unavailable."""
    first_month = date.today().replace(year=date.today().year - years, day=1).isoformat()
    response = requests.get(
        f"{url.rstrip('/')}/rest/v1/korea_market_stress_monthly",
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
        params={
            "select": "month,bok_fsi",
            "month": f"gte.{first_month}",
            "bok_fsi": "not.is.null",
        },
        timeout=TIMEOUT,
    )
    if not response.ok:
        return {}
    values: dict[str, float] = {}
    for row in response.json():
        try:
            value = float(row["bok_fsi"])
            if value != 0:
                values[str(row["month"])] = value
        except (KeyError, TypeError, ValueError):
            continue
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()
    key = require_env("ECOS_API_KEY")
    url = require_env("SUPABASE_URL")
    service_key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    values = {name: daily_month_end(key, stat, item, args.years) for name, (stat, item) in SERIES.items()}
    kospi_weekly_values = daily_friday_values(key, KOSPI_TABLE, SERIES["kospi_close"][1], args.years)
    corporate_weekly_values = daily_friday_values(key, MARKET_RATES, SERIES["bbb_minus_3y"][1], args.years)
    treasury_weekly_values = daily_friday_values(key, MARKET_RATES, SERIES["treasury_3y"][1], args.years)
    cp_weekly_values = daily_friday_values(key, MARKET_RATES, SERIES["cp_91d"][1], args.years)
    cd_weekly_values = daily_friday_values(key, MARKET_RATES, SERIES["cd_91d"][1], args.years)
    kospi_weekly = []
    for week, (observed_at, kospi_close) in sorted(kospi_weekly_values.items()):
        if date.fromisoformat(week) < WEEKLY_DISPLAY_START:
            continue
        corporate = corporate_weekly_values.get(week)
        treasury = treasury_weekly_values.get(week)
        cp_weekly = cp_weekly_values.get(week)
        cd_weekly = cd_weekly_values.get(week)
        kospi_weekly.append({
            "week": week,
            "kospi_close": round(kospi_close, 2),
            "observed_at": observed_at.isoformat(),
            "corporate_credit_spread": round(corporate[1] - treasury[1], 4) if corporate and treasury else None,
            "short_term_funding_spread": round(cp_weekly[1] - cd_weekly[1], 4) if cp_weekly and cd_weekly else None,
        })
    existing_fsi = fetch_existing_fsi(url, service_key, args.years)
    try:
        fsi = {**existing_fsi, **fetch_bok_fsi(args.years)}
    except Exception as error:
        # The MacroWatch index and KOSPI update must not stop merely because
        # the official comparison series is temporarily unavailable.
        print(f"fsi_unavailable={error}")
        fsi = existing_fsi
    months = sorted(set().union(*[set(rows) for rows in values.values()]))
    today_month = date.today().replace(day=1).isoformat()
    rows = []
    last_official_fsi: float | None = None
    for month in months:
        bbb, aa, treasury = (
            values["bbb_minus_3y"].get(month),
            values["aa_minus_3y"].get(month),
            values["treasury_3y"].get(month),
        )
        cp, cd = values["cp_91d"].get(month), values["cd_91d"].get(month)
        koribor, kofr = values["koribor_3m"].get(month), values["kofr"].get(month)
        usdkrw = values["usdkrw"].get(month)
        if not all(isinstance(value, float) for value in (bbb, aa, treasury, cp, cd, koribor, kofr, usdkrw)):
            continue
        credit_spread = bbb - treasury
        investment_grade_spread = aa - treasury
        rating_gap_spread = bbb - aa
        funding_spread = cp - cd
        interbank_liquidity_spread = koribor - kofr
        # Keep the exchange-rate input on a spread-like scale without a
        # threshold, cap, or changing historical normalization.  A move from
        # KRW 1,300 to 1,400 per USD changes this input from 1.30 to 1.40.
        usdkrw_stress = round(usdkrw / 1000.0, 2)
        component_scores = (
            score(investment_grade_spread, *STRESS_BANDS["investment_grade"]),
            score(rating_gap_spread, *STRESS_BANDS["rating_gap"]),
            score(funding_spread, *STRESS_BANDS["short_term_funding"]),
            score(interbank_liquidity_spread, *STRESS_BANDS["interbank_liquidity"]),
            usdkrw_stress,
        )
        component_stress_index = round(sum(component_scores) / len(component_scores), 2)
        official_fsi = fsi.get(month)
        has_fsi = isinstance(official_fsi, float) and official_fsi != 0
        if has_fsi:
            last_official_fsi = official_fsi
        # FSI is published with a lag.  Until the official value arrives,
        # retain the last official reading for the composite only and mark
        # that month provisional.  Do not expose the carried value as an
        # official FSI observation in the comparison chart.
        fsi_for_index = official_fsi if has_fsi else last_official_fsi
        stress_index = round(
            component_stress_index * KMSI_COMPONENT_WEIGHT + fsi_for_index * KMSI_FSI_WEIGHT,
            2,
        ) if fsi_for_index is not None else component_stress_index
        rows.append({
            "month": month,
            "stress_index": stress_index,
            "market_component_index": component_stress_index,
            "corporate_credit_spread": round(credit_spread, 4),
            "investment_grade_spread": round(investment_grade_spread, 4),
            "rating_gap_spread": round(rating_gap_spread, 4),
            "short_term_funding_spread": round(funding_spread, 4),
            "interbank_liquidity_spread": round(interbank_liquidity_spread, 4),
            "usdkrw_exchange_rate": round(usdkrw, 2),
            "kospi_close": values["kospi_close"].get(month),
            "bok_fsi": official_fsi if has_fsi else None,
            "is_provisional": month == today_month or not has_fsi,
        })
    if not rows:
        raise RuntimeError("저장할 한국 시장 스트레스 데이터가 없습니다.")
    upsert(rows, url, service_key, "korea_market_stress_monthly", "month")
    if kospi_weekly:
        upsert(kospi_weekly, url, service_key, "korea_market_stress_weekly", "week")
    print(f"upserted_months={len(rows)} kospi_weeks={len(kospi_weekly)} fsi_months={len(fsi)}")


if __name__ == "__main__":
    main()
