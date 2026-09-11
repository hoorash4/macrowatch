"""Recent Redbook observations from the no-key Helious relay.

Redbook Research does not offer a free official long-history API.  MacroWatch therefore
accumulates the recent public print without pretending that this is a historical backfill.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

from common import request_with_retry

HELIOUS_REDBOOK_URL = "https://api.helious.io/v1/series/redbook"


def _date_text(value: object) -> str | None:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    candidate = text[:10]
    try:
        date.fromisoformat(candidate)
        return candidate
    except ValueError:
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
    deduped = {row["observation_date"]: row for row in candidates}
    return [deduped[key] for key in sorted(deduped)]


def fetch_recent_redbook_rows() -> list[dict[str, Any]]:
    response = request_with_retry(lambda: requests.get(
        HELIOUS_REDBOOK_URL,
        headers={"User-Agent": "MacroWatch/1.0 (economic chart collector)"},
        timeout=30,
    ))
    response.raise_for_status()
    return _walk_redbook_rows(response.json())
