"""한국은행 공식자료로 K-MSI와 비교용 주간 시계열을 갱신한다."""

from __future__ import annotations

import argparse
import csv
import io
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests

from common import (
    AUTOMATIC_MONTHLY_CONTEXT_PERIODS,
    AUTOMATIC_MONTHLY_PERIODS,
    AUTOMATIC_WEEKLY_CONTEXT_WEEKS,
    AUTOMATIC_WEEKLY_WEEKS,
    SupabaseRest,
    month_start_months_ago,
    require_env,
    uncapped_score,
)
from signals.canonical_series import load as load_canonical, rows as canonical_rows, store as store_canonical
from signals.derived_series import rows as derived_series_rows, store as store_derived
from signals.market_index_collection import load_close as load_market_index_close


ECOS = "https://ecos.bok.or.kr/api"
BOK_SNAPSHOT_FSI = "https://snapshot.bok.or.kr/api/chart/getChart?id=1583"
MARKET_RATES = "817Y002"
SERIES = {
    "treasury_3y": (MARKET_RATES, "010200000"),
    "aa_minus_3y": (MARKET_RATES, "010300000"),
    "bbb_minus_3y": (MARKET_RATES, "010320000"),
    "cd_91d": (MARKET_RATES, "010502000"),
    "cp_91d": (MARKET_RATES, "010503000"),
    "koribor_3m": (MARKET_RATES, "010150000"),
    "kofr": (MARKET_RATES, "010901000"),
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


def daily_source_rows(key: str, stat: str, item: str, start: date, end: date) -> list[dict]:
    """Fetch a bounded daily ECOS range in calendar-year chunks."""
    rows: list[dict] = []
    for year in range(start.year, end.year + 1):
        lower = max(start, date(year, 1, 1)).strftime("%Y%m%d")
        upper = min(end, date(year, 12, 31)).strftime("%Y%m%d")
        rows.extend(ecos_rows(key, stat, "D", lower, upper, item))
    return rows


def daily_month_end(key: str, stat: str, item: str, start: date, end: date) -> dict[str, float]:
    rows = daily_source_rows(key, stat, item, start, end)
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


def daily_friday_values(key: str, stat: str, item: str, start: date, end: date) -> dict[str, tuple[date, float]]:
    """Return each Friday-ending week's last official daily value."""
    rows = daily_source_rows(key, stat, item, start, end)
    closes: dict[str, tuple[date, float]] = {}
    for row in rows:
        try:
            observed = datetime.strptime(str(row["TIME"]), "%Y%m%d").date()
            value = float(row["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
        friday = observed + timedelta(days=4 - observed.weekday())
        # Do not label a partial current week as a completed Friday close.
        if friday > end:
            continue
        week = friday.isoformat()
        if week not in closes or observed > closes[week][0]:
            closes[week] = (observed, value)
    return closes


def parsed_daily(rows: list[dict]) -> dict[date, float]:
    values: dict[date, float] = {}
    for row in rows:
        try:
            values[datetime.strptime(str(row["TIME"]), "%Y%m%d").date()] = float(row["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
    return values


def period_last(values: dict[date, float], frequency: str, end: date) -> dict[str, float]:
    grouped: dict[str, tuple[date, float]] = {}
    for observed, value in sorted(values.items()):
        period = observed.replace(day=1) if frequency == "M" else observed + timedelta(days=4 - observed.weekday())
        if period > end:
            continue
        key = period.isoformat()
        if key not in grouped or observed > grouped[key][0]:
            grouped[key] = (observed, value)
    return {period: item[1] for period, item in grouped.items()}


def weekly_observations(values: dict[date, float], end: date) -> dict[str, tuple[date, float]]:
    grouped: dict[str, tuple[date, float]] = {}
    for observed, value in sorted(values.items()):
        friday = observed + timedelta(days=4 - observed.weekday())
        if friday > end:
            continue
        key = friday.isoformat()
        if key not in grouped or observed > grouped[key][0]:
            grouped[key] = (observed, value)
    return grouped


def fetch_bok_fsi(first_month: date) -> dict[str, float]:
    """Read the Bank of Korea's published FSI comparison series directly."""
    headers = {**HEADERS, "Referer": "https://snapshot.bok.or.kr/dashboard/A6"}
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(BOK_SNAPSHOT_FSI, headers=headers, timeout=(12, TIMEOUT))
            response.raise_for_status()
            csv_text = response.json()["data"]["chart_opt"]["data"]["csv"]
            first_month_iso = first_month.replace(day=1).isoformat()
            values: dict[str, float] = {}
            for row in csv.DictReader(io.StringIO(csv_text)):
                try:
                    month = datetime.fromtimestamp(float(row["period"]) / 1000, tz=timezone.utc).date().replace(day=1).isoformat()
                    if month >= first_month_iso:
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
    return uncapped_score(value, low, high)


def upsert_automatic(
    rows: list[dict], url: str, service_key: str, table: str, conflict: str,
    *, provisional: str | None = None, compare_fields: tuple[str, ...] = (),
) -> int:
    database = SupabaseRest(url=url, service_key=service_key, timeout=TIMEOUT)
    writable = database.automatic_rows(
        table, rows, key=conflict, provisional=provisional, compare_fields=compare_fields,
    )
    if writable:
        database.upsert(table, writable, conflict=conflict)
    return len(writable)


def build_monthly_rows(values: dict[str, dict[str, float]], fsi: dict[str, float], today: date) -> list[dict]:
    """수집·저장과 분리된 기존 월간 K-MSI 계산."""
    months = sorted(set().union(*[set(rows) for rows in values.values()]))
    today_month = today.replace(day=1).isoformat()
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
        if not all(isinstance(value, float) for value in (bbb, aa, treasury, cp, cd, koribor, kofr)):
            continue
        credit_spread = bbb - treasury
        investment_grade_spread = aa - treasury
        rating_gap_spread = bbb - aa
        funding_spread = cp - cd
        interbank_liquidity_spread = koribor - kofr
        component_scores = (
            score(investment_grade_spread, *STRESS_BANDS["investment_grade"]),
            score(rating_gap_spread, *STRESS_BANDS["rating_gap"]),
            score(funding_spread, *STRESS_BANDS["short_term_funding"]),
            score(interbank_liquidity_spread, *STRESS_BANDS["interbank_liquidity"]),
        )
        component_stress_index = round(sum(component_scores) / len(component_scores), 2)
        official_fsi = fsi.get(month)
        has_fsi = isinstance(official_fsi, float) and official_fsi != 0
        if has_fsi:
            last_official_fsi = official_fsi
        # 공식 FSI가 한 번도 관측되지 않은 구간에는 70% FSI·30% 시장요소인
        # K-MSI 자체가 성립하지 않는다. 구성요소만 계산해 잠정치로 저장하지 않는다.
        if last_official_fsi is None:
            continue
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
            "kospi_close": values["kospi_close"].get(month),
            "bok_fsi": official_fsi if has_fsi else None,
            "is_provisional": month == today_month or not has_fsi,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=AUTOMATIC_MONTHLY_PERIODS)
    parser.add_argument("--stage", choices=("sources", "derived", "all"), default="all")
    args = parser.parse_args()
    if args.months < 1 or args.months > 12:
        raise SystemExit("--months 값은 1~12 사이여야 합니다.")
    url = require_env("SUPABASE_URL")
    service_key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    today = date.today()
    monthly_start = month_start_months_ago(
        today, args.months - 1 + AUTOMATIC_MONTHLY_CONTEXT_PERIODS,
    )
    weekly_start = today - timedelta(
        weeks=AUTOMATIC_WEEKLY_WEEKS + AUTOMATIC_WEEKLY_CONTEXT_WEEKS,
    )
    database = SupabaseRest(url=url, service_key=service_key, timeout=TIMEOUT)
    owned = {
        "bbb_minus_3y": ("KR_BBB_YIELD", "ECOS:817Y002/010320000"),
        "aa_minus_3y": ("KR_AA_YIELD", "ECOS:817Y002/010300000"),
        "cp_91d": ("KR_CP91", "ECOS:817Y002/010503000"),
        "cd_91d": ("KR_CD91", "ECOS:817Y002/010502000"),
        "koribor_3m": ("KR_KORIBOR3M", "ECOS:817Y002/010150000"),
    }
    if args.stage in ("sources", "all"):
        key = require_env("ECOS_API_KEY")
        source_payload = []
        try:
            daily_sources = {name: parsed_daily(daily_source_rows(
                key, SERIES[name][0], SERIES[name][1], min(monthly_start, weekly_start), today,
            )) for name in owned}
            source_payload.extend(
                row for name, (code, source) in owned.items()
                for row in canonical_rows(code, daily_sources[name], frequency="D", source=source)
            )
        except (requests.ConnectionError, requests.Timeout, RuntimeError) as error:
            # A temporary ECOS outage must not invalidate already stored canonical
            # observations.  Derived calculation proceeds from the DB and the next
            # normal run retries the same recent source window, filling any gap.
            print(f"ecos_refresh_deferred={type(error).__name__}: {error}")
        existing_fsi = load_canonical(database, "BOK_FSI", start=monthly_start)
        try:
            fsi_source = {date.fromisoformat(day): value for day, value in fetch_bok_fsi(monthly_start).items()}
        except Exception as error:
            print(f"fsi_unavailable={error}")
            fsi_source = existing_fsi
        source_payload.extend(canonical_rows("BOK_FSI", fsi_source, frequency="M", source="BOK_SNAPSHOT:1583"))
        if source_payload:
            store_canonical(database, source_payload, owner="korea_stress")
        print(f"stage=sources stored={len(source_payload)}")
        if args.stage == "sources":
            return
    code_by_name = {**{name: definition[0] for name, definition in owned.items()},
                    "treasury_3y": "KR3Y", "kofr": "KR_KOFR"}
    daily_values = {name: load_canonical(database, code, start=min(monthly_start, weekly_start), end=today)
                    for name, code in code_by_name.items()}
    daily_values["kospi_close"] = load_market_index_close(database, "KOSPI", min(monthly_start, weekly_start), today)
    corporate_credit_spread = {
        observed: daily_values["aa_minus_3y"][observed] - daily_values["treasury_3y"][observed]
        for observed in daily_values["aa_minus_3y"].keys() & daily_values["treasury_3y"].keys()
    }
    values = {name: period_last(series, "M", today) for name, series in daily_values.items()}
    kospi_weekly_values = weekly_observations(daily_values["kospi_close"], today)
    corporate_weekly_values = weekly_observations(daily_values["bbb_minus_3y"], today)
    treasury_weekly_values = weekly_observations(daily_values["treasury_3y"], today)
    cp_weekly_values = weekly_observations(daily_values["cp_91d"], today)
    cd_weekly_values = weekly_observations(daily_values["cd_91d"], today)
    kospi_weekly = []
    for week, (observed_at, kospi_close) in sorted(kospi_weekly_values.items()):
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
    kospi_weekly = kospi_weekly[-AUTOMATIC_WEEKLY_WEEKS:]
    fsi = {month.isoformat(): value for month, value in load_canonical(database, "BOK_FSI", start=monthly_start).items()}
    rows = build_monthly_rows(values, fsi, today)[-args.months:]
    if not rows:
        raise RuntimeError("저장할 한국 시장 스트레스 데이터가 없습니다.")
    calculated_payload = []
    calculated_payload.extend(derived_series_rows(
        "KOSPI_MONTH_END", {date.fromisoformat(day): value for day, value in values["kospi_close"].items()},
        frequency="M", source="RESAMPLED:MARKET_INDEX:KOSPI/M",
    ))
    calculated_payload.extend(derived_series_rows(
        "KOSPI_WEEKLY_CLOSE", {date.fromisoformat(row["week"]): float(row["kospi_close"]) for row in kospi_weekly},
        frequency="W", source="RESAMPLED:MARKET_INDEX:KOSPI/W",
    ))
    calculated_payload.extend(derived_series_rows(
        "KR_CORP_CREDIT_SPREAD", corporate_credit_spread,
        frequency="D", source="DERIVED:KR_AA_YIELD-KR3Y",
    ))
    store_derived(database, calculated_payload)
    result_rows = [{key: row[key] for key in ("month", "stress_index", "market_component_index", "is_provisional")} for row in rows]
    stored_months = upsert_automatic(
        result_rows, url, service_key, "korea_market_stress_monthly", "month", provisional="is_provisional",
        compare_fields=("stress_index", "market_component_index"),
    )
    stored_weeks = 0
    print(
        f"calculated_months={len(rows)} stored_months={stored_months} "
        f"calculated_weeks={len(kospi_weekly)} stored_weeks={stored_weeks} fsi_months={len(fsi)}"
    )


if __name__ == "__main__":
    main()
