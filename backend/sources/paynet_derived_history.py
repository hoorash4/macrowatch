"""Derived Equifax history fallback for gaps in direct monthly reports.

Only values mathematically implied by an official Equifax report are emitted.
Direct monthly levels remain authoritative; this module is used only when the
normal source cannot fetch the target month. The unified delinquency series is
the sum of the two non-overlapping published SBDI buckets.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from io import BytesIO
import re

import requests
from pypdf import PdfReader

from sources.paynet_loan_performance import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    USER_AGENT,
    _clean,
    _report_urls,
    _row,
    _shift_month,
    extract_equifax_levels,
)

_METRICS = (
    ("short", r"SBDI\s*31\s*-\s*90\s*Days(?:\s*Past\s*Due)?"),
    ("severe", r"SBDI\s*91\s*-\s*180\s*Days(?:\s*Past\s*Due)?"),
    ("default", r"SBDFI\b|Small\s+Business\s+Default\s+Index"),
)


def _fetch_one(url: str) -> tuple[tuple[float, float, float], str, str] | None:
    try:
        response = requests.get(url, timeout=4, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200 or "pdf" not in response.headers.get("content-type", "").lower():
            return None
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
        levels = extract_equifax_levels(text)
        if levels is None:
            return None
        return levels, _clean(text), url
    except Exception:
        return None


def _fetch_report_text(report_year: int, report_month: int) -> tuple[tuple[float, float, float], str, str] | None:
    """Return exact current levels and text, probing legacy filename variants in parallel."""
    urls = _report_urls(report_year, report_month)
    with ThreadPoolExecutor(max_workers=18) as pool:
        futures = [pool.submit(_fetch_one, url) for url in urls]
        for future in as_completed(futures):
            found = future.result()
            if found is not None:
                for pending in futures:
                    pending.cancel()
                return found
    return None


def _to_pp(symbol: str | None, number: str, unit: str) -> float:
    value = float(number)
    if unit.lower().startswith("bp"):
        value /= 100.0
    if symbol in ("▼", "-"):
        return -value
    return value


def _directional_delta(body: str, period: str) -> float | None:
    p = re.escape(period)
    patterns = (
        rf"([▲▼+\-])\s*([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?)\s*\(?\s*{p}\s*\)?",
        rf"\(?\s*{p}\s*\)?\s*([▲▼+\-])\s*([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?)",
    )
    for pattern in patterns:
        match = re.search(pattern, body, re.I)
        if match:
            return _to_pp(match.group(1), match.group(2), match.group(3))

    if period.upper() == "Y/Y":
        narrative = re.search(
            r"([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?)\s*"
            r"(above|higher\s+than|up\s+from|below|lower\s+than|down\s+from)[^.]{0,90}?"
            r"(?:year[- ]ago|a\s+year\s+ago|last\s+year|year[- ]over[- ]year)", body, re.I,
        )
    else:
        narrative = re.search(
            r"([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?)\s*"
            r"(above|higher\s+than|up\s+from|below|lower\s+than|down\s+from)[^.]{0,90}?"
            r"(?:last\s+month|previous\s+month|month[- ]ago|month[- ]over[- ]month)", body, re.I,
        )
    if narrative:
        sign = -1.0 if re.match(r"below|lower|down", narrative.group(3), re.I) else 1.0
        unit = narrative.group(2)
        magnitude = float(narrative.group(1)) / (100.0 if unit.lower().startswith("bp") else 1.0)
        return sign * magnitude
    return None


def _change_map(clean: str, period: str) -> dict[str, float]:
    starts: list[tuple[int, int, str]] = []
    for token, pattern in _METRICS:
        for match in re.finditer(pattern, clean, re.I):
            starts.append((match.start(), match.end(), token))
    starts.sort()
    out: dict[str, float] = {}
    for idx, (_, end, token) in enumerate(starts):
        block_end = starts[idx + 1][0] if idx + 1 < len(starts) else min(len(clean), end + 700)
        body = clean[end:min(block_end, end + 700)]
        delta = _directional_delta(body, period)
        if delta is not None and abs(delta) < 10:
            out.setdefault(token, delta)
    return out


def _derive_from_report(target: date, *, lag_months: int, period: str) -> dict[str, dict | None]:
    observed_y, observed_m = _shift_month(target.year, target.month, lag_months)
    report_y, report_m = _shift_month(observed_y, observed_m, 2)
    fetched = _fetch_report_text(report_y, report_m)
    result: dict[str, dict | None] = {SERIES_DELINQUENCY: None, SERIES_DEFAULT: None}
    if fetched is None:
        return result
    (short_now, severe_now, default_now), clean, url = fetched
    deltas = _change_map(clean, period)
    if set(deltas) != {"short", "severe", "default"}:
        return result
    short = round(short_now - deltas["short"], 6)
    severe = round(severe_now - deltas["severe"], 6)
    default = round(default_now - deltas["default"], 6)
    if not (0 <= short < 10 and 0 <= severe < 10 and 0 <= default < 10):
        return result
    provenance = f"Equifax-public:derived-{period.replace('/', '')}:{url}"
    result[SERIES_DELINQUENCY] = _row(
        SERIES_DELINQUENCY, target.year, target.month, short + severe,
        f"{provenance}:SBDI31-90={short:.2f}+SBDI91-180={severe:.2f}",
    )
    result[SERIES_DEFAULT] = _row(
        SERIES_DEFAULT, target.year, target.month, default,
        f"{provenance}:SBDFI={default:.2f}",
    )
    return result


def fetch_paynet_derived_month(observed: date) -> dict[str, dict | None]:
    target = date(observed.year, observed.month, 1)
    yoy = _derive_from_report(target, lag_months=12, period="Y/Y")
    if yoy[SERIES_DELINQUENCY] is not None and yoy[SERIES_DEFAULT] is not None:
        return yoy
    return _derive_from_report(target, lag_months=1, period="M/M")
