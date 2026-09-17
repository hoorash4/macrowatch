"""Canonical daily market-index collection for S&P 500, Nasdaq Composite and KOSPI.

Backfill and automatic collection are intentionally separate:
- Backfill may use Yahoo daily history for all three indices to reach 1990 consistently.
- Automatic collection uses Yahoo as same-day provisional data for US indices, then promotes
  the close to FRED after one later US trading session has passed.
- KOSPI automatic collection uses KRX directly.

All rows live in ``market_index_prices``; downstream returns/drawdowns are derived from these raw
index rows instead of being collected independently.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from typing import Any
from urllib.parse import quote

import requests

from common import SupabaseRest, request_with_retry

START_DATE = date(1990, 1, 1)
AUTOMATIC_LOOKBACK_DAYS = 21
UPSERT_BATCH_SIZE = 500
INDEX_CODES = ("SP500", "NASDAQ_COMPOSITE", "KOSPI")
YAHOO_SYMBOLS = {
    "SP500": "^GSPC",
    "NASDAQ_COMPOSITE": "^IXIC",
    "KOSPI": "^KS11",
}
FRED_SERIES = {
    "SP500": "SP500",
    "NASDAQ_COMPOSITE": "NASDAQCOM",
}
KOSPI_KRX_TICKER = "1001"
KRX_INDEX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_INDEX_BLD = "dbms/MDC/STAT/standard/MDCSTAT00301"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 MacroWatch/1.0",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
}


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())


def _number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fetch_yahoo_candles(index_code: str, start: date, end: date) -> list[dict[str, Any]]:
    symbol = YAHOO_SYMBOLS[index_code]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
    response = request_with_retry(lambda: requests.get(
        url,
        params={
            "period1": str(_epoch(start)),
            "period2": str(_epoch(end + timedelta(days=1))),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        },
        headers=HTTP_HEADERS,
        timeout=60,
    ))
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo {symbol}: {chart['error']}")
    result = chart.get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo {symbol} returned no chart result")
    series = result[0]
    timestamps = series.get("timestamp") or []
    quote_rows = series.get("indicators", {}).get("quote") or []
    if not quote_rows:
        raise RuntimeError(f"Yahoo {symbol} returned no quote rows")
    quote_data = quote_rows[0]
    fields = {name: quote_data.get(name) or [] for name in ("open", "high", "low", "close", "volume")}
    rows: list[dict[str, Any]] = []
    for i, stamp in enumerate(timestamps):
        try:
            observed = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date()
            open_value = float(fields["open"][i])
            high_value = float(fields["high"][i])
            low_value = float(fields["low"][i])
            close_value = float(fields["close"][i])
        except (IndexError, TypeError, ValueError, OverflowError):
            continue
        if not (start <= observed <= end):
            continue
        volume_raw = fields["volume"][i] if i < len(fields["volume"]) else None
        rows.append({
            "index_code": index_code,
            "market_date": observed.isoformat(),
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": _number(volume_raw),
            "source": f"YAHOO:{symbol}:PROVISIONAL",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def _fetch_fred_close(index_code: str, start: date, end: date) -> dict[date, float]:
    series_id = FRED_SERIES[index_code]
    response = request_with_retry(lambda: requests.get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": series_id, "cosd": start.isoformat(), "coed": end.isoformat()},
        headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
        timeout=60,
    ))
    response.raise_for_status()
    values: dict[date, float] = {}
    for item in csv.DictReader(StringIO(response.text)):
        try:
            observed = date.fromisoformat(str(item.get("observation_date") or item.get("DATE")))
        except (TypeError, ValueError):
            continue
        value = _number(item.get(series_id))
        if value is not None and start <= observed <= end:
            values[observed] = value
    return values


def _fetch_kospi_chunk(start: date, end: date) -> list[dict[str, Any]]:
    response = request_with_retry(lambda: requests.post(
        KRX_INDEX_URL,
        data={
            "bld": KRX_INDEX_BLD,
            "indIdx": KOSPI_KRX_TICKER[0],
            "indIdx2": KOSPI_KRX_TICKER[1:],
            "strtDd": start.strftime("%Y%m%d"),
            "endDd": end.strftime("%Y%m%d"),
        },
        headers=HTTP_HEADERS,
        timeout=60,
    ))
    response.raise_for_status()
    payload = response.json()
    output = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(output, list):
        raise RuntimeError(f"KRX KOSPI returned invalid payload for {start}..{end}")
    rows: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        try:
            observed = datetime.strptime(str(item["TRD_DD"]), "%Y/%m/%d").date()
        except (KeyError, TypeError, ValueError):
            continue
        open_value = _number(item.get("OPNPRC_IDX"))
        high_value = _number(item.get("HGPRC_IDX"))
        low_value = _number(item.get("LWPRC_IDX"))
        close_value = _number(item.get("CLSPRC_IDX"))
        if None in (open_value, high_value, low_value, close_value):
            continue
        rows.append({
            "index_code": "KOSPI",
            "market_date": observed.isoformat(),
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": _number(item.get("ACC_TRDVOL")),
            "source": "KRX:KOSPI:1001",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def _fetch_kospi_candles(start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year in range(start.year, end.year + 1):
        lower = max(start, date(year, 1, 1))
        upper = min(end, date(year, 12, 31))
        rows.extend(_fetch_kospi_chunk(lower, upper))
    return rows


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {str(row["market_date"]): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def fetch_index_candles(index_code: str, start: date, end: date, *, mode: str = "backfill") -> list[dict[str, Any]]:
    if mode == "automatic" and index_code == "KOSPI":
        rows = _fetch_kospi_candles(start, end)
    else:
        rows = _fetch_yahoo_candles(index_code, start, end)
    result_rows = _dedupe(rows)
    if not result_rows:
        raise RuntimeError(f"{index_code} returned no usable daily rows")
    return result_rows


def _store_batches(db: SupabaseRest, rows: list[dict[str, Any]]) -> int:
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        db.upsert("market_index_prices", rows[offset:offset + UPSERT_BATCH_SIZE],
                  conflict="index_code,market_date")
    return len(rows)


def _date_range(start: date, end: date) -> str:
    return f"(market_date.gte.{start.isoformat()},market_date.lte.{end.isoformat()})"


def backfill(db: SupabaseRest | None = None, *, start: date = START_DATE,
             end: date | None = None) -> dict[str, int]:
    """Replace a historical range. Yahoo is allowed here as the long-history source."""
    database = db or SupabaseRest()
    end = end or date.today()
    fetched = {code: fetch_index_candles(code, start, end, mode="backfill") for code in INDEX_CODES}
    for code, rows in fetched.items():
        first = date.fromisoformat(str(rows[0]["market_date"]))
        last = date.fromisoformat(str(rows[-1]["market_date"]))
        if first.year > start.year or last < end - timedelta(days=7):
            raise RuntimeError(f"{code} history failed coverage validation: {first}..{last}")
    stored: dict[str, int] = {}
    for code, rows in fetched.items():
        database.request("DELETE", "market_index_prices", params={
            "index_code": f"eq.{code}", "and": _date_range(start, end),
        })
        stored[code] = _store_batches(database, rows)
    return stored


def _promote_fred_after_one_session(index_code: str, rows: list[dict[str, Any]], start: date,
                                     end: date) -> list[dict[str, Any]]:
    """Keep the latest US trading session provisional, then replace older closes with FRED."""
    if index_code not in FRED_SERIES or len(rows) < 2:
        return rows
    trading_dates = [date.fromisoformat(str(row["market_date"])) for row in rows]
    eligible = set(trading_dates[:-1])
    try:
        fred = _fetch_fred_close(index_code, start, end)
    except requests.RequestException as error:
        # FRED verification is an upgrade of already collected Yahoo OHLC, not a
        # prerequisite for storing the market close.  Keep provisional rows and
        # retry promotion on the next normal run instead of failing the collector.
        print(f"fred_promotion_deferred index={index_code} reason={type(error).__name__}")
        return rows
    series_id = FRED_SERIES[index_code]
    symbol = YAHOO_SYMBOLS[index_code]
    promoted: list[dict[str, Any]] = []
    for row in rows:
        observed = date.fromisoformat(str(row["market_date"]))
        item = dict(row)
        if observed in eligible and observed in fred:
            item["close"] = fred[observed]
            item["source"] = f"YAHOO:{symbol}:OHLC+FRED:{series_id}:CLOSE:VERIFIED_1SESSION"
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
        promoted.append(item)
    return promoted


def automatic(db: SupabaseRest | None = None, *, today: date | None = None) -> dict[str, int]:
    """Collect recent rows; US closes become canonical after one later trading session."""
    database = db or SupabaseRest()
    today = today or date.today()
    start = today - timedelta(days=AUTOMATIC_LOOKBACK_DAYS)
    stored: dict[str, int] = {}
    for code in INDEX_CODES:
        rows = fetch_index_candles(code, start, today, mode="automatic")
        if code in FRED_SERIES:
            rows = _promote_fred_after_one_session(code, rows, start, today)
        # Recent window is deliberately upserted every run so provisional rows can later be
        # promoted to FRED-verified canonical closes and KRX corrections can replace prior rows.
        stored[code] = _store_batches(database, rows)
    return stored


def load_close(db: SupabaseRest, index_code: str, start: date, end: date) -> dict[date, float]:
    values: dict[date, float] = {}
    offset = 0
    while True:
        page = db.request("GET", "market_index_prices", params={
            "select": "market_date,close",
            "index_code": f"eq.{index_code}",
            "and": _date_range(start, end),
            "order": "market_date.asc",
            "offset": str(offset), "limit": "1000",
        }) or []
        for row in page:
            if row.get("close") is not None:
                values[date.fromisoformat(str(row["market_date"]))] = float(row["close"])
        if len(page) < 1000:
            return values
        offset += len(page)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("automatic", "backfill"), default="automatic")
    parser.add_argument("--start", default=START_DATE.isoformat())
    parser.add_argument("--end")
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    result = backfill(start=start, end=end) if args.mode == "backfill" else automatic(today=end)
    print({"mode": args.mode, "start": start.isoformat(), "end": end.isoformat(), "stored": result})


if __name__ == "__main__":
    main()
