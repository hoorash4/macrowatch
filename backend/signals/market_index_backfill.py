"""One-time canonical daily index backfill from 1990.

US indices use Yahoo chart history. KOSPI uses Naver Finance history because Yahoo's ^KS11
history starts in 1996 and the KRX web endpoint rejects unattended GitHub runners. Production
KOSPI automatic collection remains separate and continues to use KRX.
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from typing import Any

import requests

from common import SupabaseRest, request_with_retry
from signals.market_index_collection import START_DATE, UPSERT_BATCH_SIZE, _fetch_yahoo_candles

NAVER_HISTORY_URL = "https://api.finance.naver.com/siseJson.naver"


def _number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_naver_kospi(start: date, end: date) -> list[dict[str, Any]]:
    response = request_with_retry(lambda: requests.get(
        NAVER_HISTORY_URL,
        params={
            "symbol": "KOSPI",
            "requestType": "1",
            "startTime": start.strftime("%Y%m%d"),
            "endTime": end.strftime("%Y%m%d"),
            "timeframe": "day",
        },
        headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0", "Referer": "https://finance.naver.com/"},
        timeout=120,
    ))
    response.raise_for_status()
    try:
        payload = ast.literal_eval(response.text.strip())
    except (SyntaxError, ValueError) as exc:
        raise RuntimeError(f"Naver KOSPI returned invalid history: {response.text[:160]!r}") from exc
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError(f"Naver KOSPI returned empty history: {response.text[:160]!r}")

    rows: list[dict[str, Any]] = []
    for item in payload[1:]:
        if not isinstance(item, list) or len(item) < 6:
            continue
        try:
            observed = datetime.strptime(str(item[0]), "%Y%m%d").date()
        except (TypeError, ValueError):
            continue
        open_value = _number(item[1])
        high_value = _number(item[2])
        low_value = _number(item[3])
        close_value = _number(item[4])
        if None in (open_value, high_value, low_value, close_value):
            continue
        # Old KOSPI history occasionally contains tiny OHLC rounding inversions (for example,
        # reported low a few hundredths above open). Preserve open/close and normalize only the
        # candle envelope so it satisfies the table's OHLC invariants.
        high_value = max(high_value, open_value, low_value, close_value)
        low_value = min(low_value, open_value, high_value, close_value)
        rows.append({
            "index_code": "KOSPI",
            "market_date": observed.isoformat(),
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": _number(item[5]),
            "source": "NAVER:KOSPI:BACKFILL",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    rows.sort(key=lambda row: str(row["market_date"]))
    if not rows:
        raise RuntimeError("Naver KOSPI returned no usable daily rows")
    return rows


def _store(db: SupabaseRest, rows: list[dict[str, Any]]) -> int:
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        db.upsert("market_index_prices", rows[offset:offset + UPSERT_BATCH_SIZE], conflict="index_code,market_date")
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
    if first.year > start.year or last.toordinal() < end.toordinal() - 7:
        raise RuntimeError(f"{index_code} history failed coverage validation: {first}..{last}")


def run(start: date = START_DATE, end: date | None = None) -> dict[str, int]:
    end = end or date.today()
    fetched = {
        "SP500": _fetch_yahoo_candles("SP500", start, end),
        "NASDAQ_COMPOSITE": _fetch_yahoo_candles("NASDAQ_COMPOSITE", start, end),
        "KOSPI": fetch_naver_kospi(start, end),
    }
    for code, rows in fetched.items():
        _validate(code, rows, start, end)

    db = SupabaseRest()
    return {code: _replace(db, code, rows, start, end) for code, rows in fetched.items()}


if __name__ == "__main__":
    result = run()
    print({"start": START_DATE.isoformat(), "end": date.today().isoformat(), "stored": result})
