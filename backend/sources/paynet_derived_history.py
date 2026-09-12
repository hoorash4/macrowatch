"""Derived Equifax history fallback for gaps in direct monthly reports.

Only values mathematically implied by an official Equifax/PayNet report are emitted.
For legacy reports, the published National - Overall table row is preferred because
it contains current month, prior month, MoM change, year-ago month and YoY change.
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


def _fetch_one(url: str) -> tuple[tuple[float, float, float] | None, str, str] | None:
    try:
        response = requests.get(url, timeout=4, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200 or "pdf" not in response.headers.get("content-type", "").lower():
            return None
        text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages)
        clean = _clean(text)
        levels = extract_equifax_levels(text)
        # Legacy table reports may not be understood by the modern level parser,
        # but their text is still useful for direct year-ago/previous-month rows.
        if levels is None and "National" not in clean:
            return None
        return levels, clean, url
    except Exception:
        return None


def _fetch_report_text(report_year: int, report_month: int) -> tuple[tuple[float, float, float] | None, str, str] | None:
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


def _pct_values(text: str) -> list[float]:
    return [float(v) for v in re.findall(r"(-?[0-9]+(?:\.[0-9]+)?)\s*%", text)]


def _legacy_overall_values(clean: str, heading: str, row_label: str) -> tuple[float, float] | None:
    """Return (previous-month, year-ago) levels from a legacy Overall table row."""
    h = re.search(heading, clean, re.I)
    if not h:
        return None
    # Tables are compact in extracted text. Restrict the scan to this table area.
    section = clean[h.end():h.end() + 9000]
    row = re.search(row_label + r"\b([^\n]{0,700}|.{0,700})", section, re.I)
    if not row:
        return None
    vals = _pct_values(row.group(0))
    # Legacy columns: CURRENT, PREVIOUS, MoM CHANGE, YEAR-AGO, YoY CHANGE.
    if len(vals) < 5:
        # pypdf often collapses rows; take percentages immediately after row label.
        pos = section.lower().find(re.sub(r"\\s\*", " ", row_label).lower())
        if pos >= 0:
            vals = _pct_values(section[pos:pos + 900])
    if len(vals) >= 5:
        previous, year_ago = vals[1], vals[3]
        if 0 <= previous < 10 and 0 <= year_ago < 10:
            return previous, year_ago
    return None


def _legacy_levels(clean: str, period: str) -> tuple[float, float, float] | None:
    """Extract prior-period levels directly from old PayNet Overall tables."""
    short = _legacy_overall_values(
        clean,
        r"SMALL\s+BUSINESS\s+DELINQUENCY\s+INDEX\s+31\s*-\s*90\s+DAYS\s+PAST\s+DUE",
        r"SBDI\s+National\s*-\s*Overall",
    )
    severe = _legacy_overall_values(
        clean,
        r"SMALL\s+BUSINESS\s+DELINQUENCY\s+INDEX\s+91\s*-\s*180\s+DAYS\s+PAST\s+DUE",
        r"SBDI\s+National\s*-\s*Overall",
    )
    default = _legacy_overall_values(
        clean,
        r"SMALL\s+BUSINESS\s+DEFAULT\s+INDEX|SBDFI",
        r"(?:SBDFI|Default)\s+National\s*-\s*Overall",
    )
    if not (short and severe and default):
        return None
    idx = 1 if period.upper() == "Y/Y" else 0
    values = (short[idx], severe[idx], default[idx])
    if all(0 <= v < 10 for v in values):
        return values
    return None


def _to_pp(symbol: str | None, number: str, unit: str) -> float:
    value = float(number)
    if unit.lower().startswith("bp"):
        value /= 100.0
    if symbol in ("▼", "-"):
        return -value
    return value


def _directional_delta(body: str, period: str) -> float | None:
    aliases = r"(?:Y\s*/\s*Y|YoY|Y-O-Y|year[- ]over[- ]year)" if period.upper() == "Y/Y" else r"(?:M\s*/\s*M|MoM|M-O-M|month[- ]over[- ]month)"
    patterns = (
        rf"([▲▼+\-])\s*([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?)\s*\(?\s*{aliases}\s*\)?",
        rf"\(?\s*{aliases}\s*\)?\s*([▲▼+\-])\s*([0-9]+(?:\.[0-9]+)?)\s*(bps?|bp|pp|percentage\s+points?)",
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
    levels, clean, url = fetched

    # Old PayNet reports publish the comparison levels directly in Overall rows.
    legacy = _legacy_levels(clean, period)
    if legacy is not None:
        short, severe, default = legacy
        provenance = f"PayNet-public:legacy-table-{period.replace('/', '')}:{url}"
        result[SERIES_DELINQUENCY] = _row(
            SERIES_DELINQUENCY, target.year, target.month, short + severe,
            f"{provenance}:SBDI31-90={short:.2f}+SBDI91-180={severe:.2f}",
        )
        result[SERIES_DEFAULT] = _row(
            SERIES_DEFAULT, target.year, target.month, default,
            f"{provenance}:SBDFI={default:.2f}",
        )
        return result

    if levels is None:
        return result
    short_now, severe_now, default_now = levels
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
