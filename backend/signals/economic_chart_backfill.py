"""One-off/backfill runner for economic-chart series that are not already stored elsewhere.

This module is intentionally separate from the scheduled automatic collector.  It never
removes rows and isolates every source so one unavailable series does not discard work
already completed for the others.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import requests

from common import SupabaseRest, request_with_retry
from signals.economic_chart_pipeline import (
    ECOS_SERIES,
    KRX_INDEX_FUNDAMENTALS,
    _derive_spread,
    _ecos_rows,
    _fred_rows,
    _insert_missing,
    _krx_index_rows,
)

# Exact daily/weekly observations already live in other MacroWatch tables and are
# deliberately not duplicated here: US2Y, US10Y, USDKRW, RRP and TGA.
# US 10Y-2Y is backfilled directly because there is no equivalent daily stored series.
BACKFILL_FRED_SERIES = {
    "US10Y2Y": ("T10Y2Y", "D"),
    "HY_OAS": ("BAMLH0A0HYM2", "D"),
    "EM_OAS": ("BAMLEMCBPIOAS", "D"),
    "WTI": ("DCOILWTICO", "D"),
    "WEI": ("WEI", "W"),
    "EMRATIO": ("EMRATIO", "M"),
}

# Redbook has no official free long-history API.  Helious exposes a no-key sample
# endpoint, so bootstrap stores only whatever recent official-release observations the
# free endpoint currently makes available; subsequent automatic runs can accumulate it.
HELIOUS_REDBOOK_URL = "https://api.helious.io/v1/series/redbook"


def _date_text(value: object) -> str | None:
    text = str(value or "").strip()
    if len(text) >= 10:
        candidate = text[:10]
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            return None
    return None


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace("%", "").replace(",", "")
    if not text or text in {"-", "—", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _walk_redbook_rows(payload: object) -> list[dict[str, Any]]:
    """Accept the stable v1 envelope while tolerating small field-name variations."""
    candidates: list[dict[str, Any]] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        date_value = next((node.get(key) for key in (
            "date", "release_date", "released_at", "reference_date", "period_date"
        ) if node.get(key) is not None), None)
        actual_value = next((node.get(key) for key in (
            "actual", "value", "actual_value", "print"
        ) if node.get(key) is not None), None)
        observed = _date_text(date_value)
        actual = _number(actual_value)
        if observed and actual is not None:
            candidates.append({
                "series_code": "REDBOOK",
                "observation_date": observed,
                "value": actual,
                "frequency": "W",
                "source": "HELIOUS:REDBOOK/REDBOOK_RESEARCH",
            })
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value)

    walk(payload)
    deduped: dict[str, dict[str, Any]] = {}
    for row in candidates:
        deduped[row["observation_date"]] = row
    return [deduped[key] for key in sorted(deduped)]


def _redbook_rows() -> list[dict[str, Any]]:
    response = request_with_retry(lambda: requests.get(
        HELIOUS_REDBOOK_URL,
        headers={"User-Agent": "MacroWatch/1.0 (economic chart collector)"},
        timeout=30,
    ))
    response.raise_for_status()
    return _walk_redbook_rows(response.json())


def backfill() -> tuple[dict[str, int], dict[str, str]]:
    today = date.today()
    start = today - timedelta(days=3660)
    db = SupabaseRest()
    inserted: dict[str, int] = {}
    errors: dict[str, str] = {}

    def run(name: str, action) -> None:
        try:
            inserted[name] = int(action())
        except Exception as error:  # preserve successful series and continue
            inserted.setdefault(name, 0)
            errors[name] = f"{error.__class__.__name__}: {error}"
            print(json.dumps({"stage": "economic_chart_backfill_error", "series": name, "error": errors[name]}, ensure_ascii=False))

    for code, (source_id, frequency) in BACKFILL_FRED_SERIES.items():
        run(code, lambda code=code, source_id=source_id, frequency=frequency: _insert_missing(
            db, _fred_rows(code, source_id, frequency, start, today), start
        ))

    for code, (stat_code, item_code, frequency) in ECOS_SERIES.items():
        run(code, lambda code=code, stat_code=stat_code, item_code=item_code, frequency=frequency: _insert_missing(
            db, _ecos_rows(code, stat_code, item_code, frequency, start, today), start
        ))

    def backfill_krx() -> int:
        rows_by_code = _krx_index_rows(start, today)
        total = 0
        for code in KRX_INDEX_FUNDAMENTALS:
            try:
                count = _insert_missing(db, rows_by_code.get(code, []), start)
                inserted[code] = count
                total += count
            except Exception as error:
                inserted.setdefault(code, 0)
                errors[code] = f"{error.__class__.__name__}: {error}"
        return total

    try:
        backfill_krx()
    except Exception as error:
        for code in KRX_INDEX_FUNDAMENTALS:
            inserted.setdefault(code, 0)
            errors.setdefault(code, f"{error.__class__.__name__}: {error}")
        print(json.dumps({"stage": "economic_chart_backfill_error", "series": "KRX_INDEX_FUNDAMENTALS", "error": str(error)}, ensure_ascii=False))

    # Korea spread is derived only after both official ECOS legs have had a chance to load.
    run("KR10Y3Y", lambda: _derive_spread(db, "KR10Y3Y", "KR10Y", "KR3Y", "D", start, today))

    # No historical Redbook backfill is assumed: take only the recent no-key sample.
    run("REDBOOK", lambda: _insert_missing(db, _redbook_rows(), today - timedelta(days=35)))

    print(json.dumps({
        "mode": "backfill",
        "start": start.isoformat(),
        "end": today.isoformat(),
        "inserted": inserted,
        "errors": errors,
    }, ensure_ascii=False, sort_keys=True))
    return inserted, errors


def main() -> None:
    backfill()


if __name__ == "__main__":
    main()
