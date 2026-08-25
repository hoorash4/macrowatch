"""Build MacroWatch's Korean market-stress index from official BOK data.

The index deliberately excludes the exchange rate.  FSI is collected only as
an external official comparison series; it is never an input to the index.
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from urllib.parse import quote

import requests


ECOS = "https://ecos.bok.or.kr/api"
MARKET_RATES = "817Y002"
KOSPI_TABLE = "802Y001"
SERIES = {
    "treasury_3y": (MARKET_RATES, "010200000"),
    "bbb_minus_3y": (MARKET_RATES, "010320000"),
    "cd_91d": (MARKET_RATES, "010502000"),
    "cp_91d": (MARKET_RATES, "010503000"),
    "kospi_close": (KOSPI_TABLE, "0001000"),
}
TIMEOUT = 45
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
    response = requests.get("/".join([ECOS, *path]), headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


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
    rows = ecos_rows(key, stat, "D", f"{today.year - years}0101", today.strftime("%Y%m%d"), item)
    values: dict[str, tuple[str, float]] = {}
    for row in rows:
        try:
            observed, value = str(row["TIME"]), float(row["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
        month = observed[:6] + "-01"
        if month not in values or observed > values[month][0]:
            values[month] = (observed, value)
    return {month: value for month, (_observed, value) in values.items()}


def find_fsi(key: str, years: int) -> dict[str, float]:
    """Discover BOK's FSI table instead of hard-coding a fragile table code."""
    catalog = request(["StatisticTableList", quote(key, safe=""), "json", "kr", "1", "10000"])
    table = next((row for row in catalog.get("StatisticTableList", {}).get("row", [])
                  if "금융불안" in str(row.get("STAT_NAME", "")) and str(row.get("CYCLE", "")) == "M"), None)
    if not table:
        return {}
    stat = str(table["STAT_CODE"])
    items = request(["StatisticItemList", quote(key, safe=""), "json", "kr", "1", "1000", stat])
    item = next((row for row in items.get("StatisticItemList", {}).get("row", [])
                 if "금융불안" in str(row.get("ITEM_NAME", row.get("ITEM_NAME1", "")))), None)
    code = str((item or {}).get("ITEM_CODE", (item or {}).get("ITEM_CODE1", "")))
    if not code:
        return {}
    today = date.today()
    rows = ecos_rows(key, stat, "M", f"{today.year - years}01", today.strftime("%Y%m"), code)
    values = {}
    for row in rows:
        try:
            values[f"{str(row['TIME'])[:4]}-{str(row['TIME'])[4:6]}-01"] = float(row["DATA_VALUE"])
        except (KeyError, TypeError, ValueError):
            continue
    return values


def score(value: float, low: float, high: float) -> float:
    return max(0.0, (value - low) / (high - low) * 100.0)


def upsert(rows: list[dict], url: str, service_key: str) -> None:
    response = requests.post(
        f"{url.rstrip('/')}/rest/v1/korea_market_stress_monthly?on_conflict=month",
        headers={"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows,
        timeout=TIMEOUT,
    )
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()
    key = require_env("ECOS_API_KEY")
    url = require_env("SUPABASE_URL")
    service_key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    values = {name: daily_month_end(key, stat, item, args.years) for name, (stat, item) in SERIES.items()}
    try:
        fsi = find_fsi(key, args.years)
    except Exception as error:
        # The MacroWatch index and KOSPI update must not stop merely because
        # the official comparison series is temporarily unavailable.
        print(f"fsi_unavailable={error}")
        fsi = {}
    months = sorted(set().union(*[set(rows) for rows in values.values()]))
    today_month = date.today().replace(day=1).isoformat()
    rows = []
    for month in months:
        bbb, treasury = values["bbb_minus_3y"].get(month), values["treasury_3y"].get(month)
        cp, cd = values["cp_91d"].get(month), values["cd_91d"].get(month)
        if not all(isinstance(value, float) for value in (bbb, treasury, cp, cd)):
            continue
        credit_spread, funding_spread = bbb - treasury, cp - cd
        rows.append({
            "month": month,
            "stress_index": round((score(credit_spread, 1.0, 8.0) + score(funding_spread, 0.0, 2.0)) / 2, 2),
            "corporate_credit_spread": round(credit_spread, 4),
            "short_term_funding_spread": round(funding_spread, 4),
            "kospi_close": values["kospi_close"].get(month),
            "bok_fsi": fsi.get(month),
            "is_provisional": month == today_month or month not in fsi,
        })
    if not rows:
        raise RuntimeError("저장할 한국 시장 스트레스 데이터가 없습니다.")
    upsert(rows, url, service_key)
    print(f"upserted_months={len(rows)} fsi_months={len(fsi)}")


if __name__ == "__main__":
    main()
