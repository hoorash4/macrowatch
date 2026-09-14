"""Canonical daily market-index collection for S&P 500, Nasdaq Composite and KOSPI.

The three raw index histories live only in market_index_prices. Historical replacement and
scheduled incremental collection are deliberately separate modes.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from common import SupabaseRest, request_with_retry

START_DATE = date(1990, 1, 1)
AUTOMATIC_LOOKBACK_DAYS = 14
UPSERT_BATCH_SIZE = 500
INDEX_SYMBOLS = {
    "SP500": "^GSPC",
    "NASDAQ_COMPOSITE": "^IXIC",
    "KOSPI": "^KS11",
}


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())


def fetch_index_candles(index_code: str, start: date, end: date) -> list[dict[str, Any]]:
    symbol = INDEX_SYMBOLS[index_code]
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
        headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
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
        try:
            volume = float(volume_raw) if volume_raw is not None else None
        except (TypeError, ValueError):
            volume = None
        rows.append({
            "index_code": index_code,
            "market_date": observed.isoformat(),
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": volume,
            "source": f"YAHOO:{symbol}",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    unique = {str(row["market_date"]): row for row in rows}
    result_rows = [unique[key] for key in sorted(unique)]
    if not result_rows:
        raise RuntimeError(f"Yahoo {symbol} returned no usable daily rows")
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
    """Replace the requested historical range from fresh external source data."""
    database = db or SupabaseRest()
    end = end or date.today()
    fetched = {code: fetch_index_candles(code, start, end) for code in INDEX_SYMBOLS}
    for code, rows in fetched.items():
        first = date.fromisoformat(str(rows[0]["market_date"]))
        last = date.fromisoformat(str(rows[-1]["market_date"]))
        if first.year > start.year or last < end - timedelta(days=7):
            raise RuntimeError(
                f"{code} history failed coverage validation: {first.isoformat()}..{last.isoformat()}"
            )
    stored: dict[str, int] = {}
    for code, rows in fetched.items():
        database.request("DELETE", "market_index_prices", params={
            "index_code": f"eq.{code}",
            "and": _date_range(start, end),
        })
        stored[code] = _store_batches(database, rows)
    return stored


def automatic(db: SupabaseRest | None = None, *, today: date | None = None) -> dict[str, int]:
    """Fetch a short recent window and add only missing/current observations."""
    database = db or SupabaseRest()
    today = today or date.today()
    start = today - timedelta(days=AUTOMATIC_LOOKBACK_DAYS)
    stored: dict[str, int] = {}
    for code in INDEX_SYMBOLS:
        rows = fetch_index_candles(code, start, today)
        dates = [str(row["market_date"]) for row in rows]
        existing = database.request("GET", "market_index_prices", params={
            "select": "market_date",
            "index_code": f"eq.{code}",
            "market_date": f"in.({','.join(dates)})",
        }) or []
        existing_dates = {str(row["market_date"]) for row in existing}
        writable = [row for row in rows if row["market_date"] == today.isoformat()
                    or row["market_date"] not in existing_dates]
        stored[code] = _store_batches(database, writable) if writable else 0
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
            "offset": str(offset),
            "limit": "1000",
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
