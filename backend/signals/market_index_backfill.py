"""One-time canonical daily index backfill from 1990.

US indices use Yahoo chart history. KOSPI uses Stooq history because Yahoo's ^KS11 history starts
in 1996 and KRX's web endpoint is not reliable from unattended GitHub runners. Production KOSPI
automatic collection remains separate and continues to use KRX.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from io import StringIO
from typing import Any

import requests

from common import SupabaseRest, request_with_retry
from signals.market_index_collection import (
    START_DATE,
    UPSERT_BATCH_SIZE,
    _fetch_yahoo_candles,
)

STOOQ_KOSPI_URL = "https://stooq.com/q/d/l/"


def _number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_stooq_kospi(start: date, end: date) -> list[dict[str, Any]]:
    response = request_with_retry(lambda: requests.get(
        STOOQ_KOSPI_URL,
        params={
            "s": "^kospi",
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        },
        headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
        timeout=90,
    ))
    response.raise_for_status()
    if "Date" not in response.text or "Close" not in response.text:
        raise RuntimeError(f"Stooq KOSPI returned non-CSV response: {response.text[:160]!r}")

    rows: list[dict[str, Any]] = []
    for item in csv.DictReader(StringIO(response.text)):
        try:
            observed = date.fromisoformat(str(item.get("Date")))
        except (TypeError, ValueError):
            continue
        open_value = _number(item.get("Open"))
        high_value = _number(item.get("High"))
        low_value = _number(item.get("Low"))
        close_value = _number(item.get("Close"))
        if None in (open_value, high_value, low_value, close_value):
            continue
        rows.append({
            "index_code": "KOSPI",
            "market_date": observed.isoformat(),
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": _number(item.get("Volume")),
            "source": "STOOQ:^KOSPI:BACKFILL",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    rows.sort(key=lambda row: str(row["market_date"]))
    if not rows:
        raise RuntimeError("Stooq KOSPI returned no usable daily rows")
    return rows


def _store(db: SupabaseRest, rows: list[dict[str, Any]]) -> int:
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        db.upsert(
            "market_index_prices",
            rows[offset:offset + UPSERT_BATCH_SIZE],
            conflict="index_code,market_date",
        )
    return len(rows)


def _replace(db: SupabaseRest, index_code: str, rows: list[dict[str, Any]], start: date, end: date) -> int:
    db.request("DELETE", "market_index_prices", params={
        "index_code": f"eq.{index_code}",
        "and": f"(market_date.gte.{start.isoformat()},market_date.lte.{end.isoformat()})",
    })
    return _store(db, rows)


def _validate(index_code: str, rows: list[dict[str, Any]], start: date, end: date) -> None:
    first = date.fromisoformat(str(rows[0]["market_date"]))
    last = date.fromisoformat(str(rows[-1]["market_date"]))
    if first.year > start.year or last < end.fromordinal(end.toordinal() - 7):
        raise RuntimeError(f"{index_code} history failed coverage validation: {first}..{last}")


def run(start: date = START_DATE, end: date | None = None) -> dict[str, int]:
    end = end or date.today()
    fetched = {
        "SP500": _fetch_yahoo_candles("SP500", start, end),
        "NASDAQ_COMPOSITE": _fetch_yahoo_candles("NASDAQ_COMPOSITE", start, end),
        "KOSPI": fetch_stooq_kospi(start, end),
    }
    for code, rows in fetched.items():
        _validate(code, rows, start, end)

    db = SupabaseRest()
    stored: dict[str, int] = {}
    for code, rows in fetched.items():
        stored[code] = _replace(db, code, rows, start, end)
    return stored


if __name__ == "__main__":
    result = run()
    print({"start": START_DATE.isoformat(), "end": date.today().isoformat(), "stored": result})
