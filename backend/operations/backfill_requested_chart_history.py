"""One-time authoritative history replacement for the requested chart series.

This entrypoint is removed after the verified run. Automatic collectors never import it.
"""
from __future__ import annotations

import math
import time
from datetime import date
from typing import Callable

from common import SupabaseRest
from signals.canonical_series import store as store_canonical
from signals.economic_chart_pipeline import _fred_rows
from sources.krx_index_fundamentals import SERIES as KRX_SERIES
from sources.krx_index_fundamentals import SOURCE as KRX_SOURCE
from sources.krx_index_fundamentals import _call_frame, _index_date


NFCI_START = date(1990, 1, 1)
KOSPI_START = date(2001, 1, 1)
TABLE = "economic_chart_points"


def existing_dates(database: SupabaseRest, series_code: str, start: date, end: date) -> set[str]:
    result: set[str] = set()
    offset = 0
    while True:
        page = database.request("GET", TABLE, params={
            "select": "observation_date",
            "series_code": f"eq.{series_code}",
            "observation_date": f"gte.{start.isoformat()}",
            "and": f"(observation_date.lte.{end.isoformat()})",
            "order": "observation_date.asc",
            "offset": str(offset),
            "limit": "1000",
        }) or []
        result.update(str(row["observation_date"]) for row in page)
        if len(page) < 1000:
            return result
        offset += len(page)


def delete_dates(database: SupabaseRest, series_code: str, dates: set[str]) -> None:
    ordered = sorted(dates)
    for offset in range(0, len(ordered), 100):
        batch = ordered[offset:offset + 100]
        database.request(
            "DELETE", TABLE,
            params={"series_code": f"eq.{series_code}", "observation_date": f"in.({','.join(batch)})"},
            prefer="return=minimal",
        )


def replace_history(database: SupabaseRest, rows: list[dict], *, owner: str,
                    start: date, end: date) -> None:
    if not rows:
        raise RuntimeError("authoritative history is empty")
    codes = {str(row["series_code"]) for row in rows}
    if len(codes) != 1:
        raise RuntimeError(f"one series expected, received {sorted(codes)}")
    series_code = next(iter(codes))
    authoritative_dates = {str(row["observation_date"]) for row in rows}
    if len(authoritative_dates) != len(rows):
        raise RuntimeError(f"duplicate authoritative dates: {series_code}")
    previous_dates = existing_dates(database, series_code, start, end)
    # Upsert the fully validated source response first. If a later stale-row cleanup
    # fails, no confirmed history is lost and the idempotent job can be rerun safely.
    store_canonical(database, rows, owner=owner)
    delete_dates(database, series_code, previous_dates - authoritative_dates)
    print(
        f"series={series_code} rows={len(rows)} first={min(authoritative_dates)} "
        f"last={max(authoritative_dates)} stale_removed={len(previous_dates - authoritative_dates)}"
    )


def retry_krx(fetch: Callable[[], object], label: str) -> object:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return fetch()
        except Exception as error:
            last_error = error
            if attempt == 3:
                break
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(f"KRX history fetch failed for {label}: {last_error}") from last_error


def parse_krx_history_frame(frame: object) -> dict[str, list[dict]]:
    """Treat KRX's historical '-' cells as missing observations, never as zero."""
    if frame is None or bool(getattr(frame, "empty", True)):
        raise RuntimeError("KRX historical response is empty")
    columns = {str(column) for column in getattr(frame, "columns", [])}
    expected = {field for field, _frequency in KRX_SERIES.values()}
    if not expected.issubset(columns):
        raise RuntimeError(f"KRX historical columns are missing: {sorted(expected - columns)}")
    result = {code: [] for code in KRX_SERIES}
    for index, row in frame.iterrows():
        observed = _index_date(index)
        for code, (field, frequency) in KRX_SERIES.items():
            text = str(row[field]).strip().replace(",", "")
            try:
                value = float(text)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or value <= 0:
                continue
            result[code].append({
                "series_code": code,
                "observation_date": observed.isoformat(),
                "value": value,
                "frequency": frequency,
                "source": KRX_SOURCE,
            })
    return result


def fetch_kospi_history(start: date, end: date) -> dict[str, list[dict]]:
    combined = {"KOSPI_PER": [], "KOSPI_PBR": []}
    for year in range(start.year, end.year + 1):
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        frame = retry_krx(
            lambda chunk_start=chunk_start, chunk_end=chunk_end: _call_frame(chunk_start, chunk_end),
            str(year),
        )
        chunk = parse_krx_history_frame(frame)
        for code in combined:
            combined[code].extend(chunk[code])
        print(f"krx_year={year} rows={len(chunk['KOSPI_PER'])}")
        time.sleep(0.25)
    return combined


def main() -> None:
    today = date.today()
    database = SupabaseRest()

    nfci_rows = _fred_rows("NFCI_RISK", "NFCIRISK", "W", NFCI_START, today)
    if date.fromisoformat(str(nfci_rows[0]["observation_date"])) > date(1990, 1, 31):
        raise RuntimeError("NFCI risk history did not reach January 1990")
    replace_history(database, nfci_rows, owner="financial_stress", start=NFCI_START, end=today)

    kospi = fetch_kospi_history(KOSPI_START, today)
    per_dates = {str(row["observation_date"]) for row in kospi["KOSPI_PER"]}
    pbr_dates = {str(row["observation_date"]) for row in kospi["KOSPI_PBR"]}
    if per_dates != pbr_dates:
        raise RuntimeError("KOSPI PER/PBR authoritative date sets differ")
    if not per_dates or min(per_dates) > "2001-01-31":
        raise RuntimeError("KOSPI valuation history did not reach January 2001")
    for code in ("KOSPI_PER", "KOSPI_PBR"):
        replace_history(database, kospi[code], owner="economic_chart", start=KOSPI_START, end=today)


if __name__ == "__main__":
    main()
