"""Robust public Epiq AACER Commercial Chapter 11 monthly source."""
from __future__ import annotations

from datetime import date
from html import unescape
import re
from urllib.parse import urljoin

import requests

from common import request_with_retry

USER_AGENT = "Mozilla/5.0 (compatible; MacroWatch/1.0; +https://hoorash4.github.io/macrowatch/)"
MONTH_NAMES = "January February March April May June July August September October November December".split()
MONTHS = {name: i for i, name in enumerate(MONTH_NAMES, 1)}
MONTH_PATTERN = "(?:" + "|".join(MONTH_NAMES) + ")"


def _request(url: str) -> requests.Response:
    response = request_with_retry(lambda: requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT}))
    response.raise_for_status()
    return response


def _plain_html(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _publication_date(text: str) -> tuple[int, int] | None:
    match = re.search(rf"\b({MONTH_PATTERN})\.?\s+\d{{1,2}},\s+(20\d{{2}})\b", text, re.I)
    if not match:
        return None
    return int(match.group(2)), MONTHS[match.group(1).capitalize()]


def _resolve_year(month: int, explicit: str | None, publication: tuple[int, int] | None) -> int | None:
    if explicit:
        return int(explicit)
    if publication is None:
        return None
    pub_year, pub_month = publication
    return pub_year if month <= pub_month else pub_year - 1


def _store(out: dict[tuple[int, int], int], year: int | None, month_name: str, count_s: str) -> None:
    if year is None:
        return
    month = MONTHS[month_name.capitalize()]
    count = int(count_s.replace(",", ""))
    if 0 < count < 100000:
        out.setdefault((year, month), count)


def extract_epiq_ch11(text: str) -> list[tuple[int, int, int]]:
    """Extract exact month/count pairs without assigning a current count to a prior-year month."""
    clean = re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))
    publication = _publication_date(clean)
    out: dict[tuple[int, int], int] = {}
    for sentence in re.split(r"(?<=[.!?])\s+", clean):
        if not re.search(r"commercial\s+chapter\s+11", sentence, re.I):
            continue

        # Count immediately identifies the commercial Chapter 11 observation.
        for m in re.finditer(
            rf"([0-9][0-9,]*)\s+commercial\s+Chapter\s+11(?:\s+bankruptcy)?\s+filings?\b"
            rf"[^.!?]{{0,100}}?\b(?:in|during|for|recorded\s+in)\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?",
            sentence, re.I,
        ):
            count_s, month_name, explicit = m.groups()
            month = MONTHS[month_name.capitalize()]
            _store(out, _resolve_year(month, explicit, publication), month_name, count_s)

        # The reporting month precedes a clearly stated current total.
        for m in re.finditer(
            rf"commercial\s+Chapter\s+11(?:\s+bankruptcy)?\s+filings?\b"
            rf"[^.!?]{{0,100}}?\b(?:in|during|for)\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?"
            rf"[^.!?]{{0,100}}?(?:totaled|were|reached|climbed\s+to|rose\s+to|increased[^.!?]{{0,40}}?to|decreased[^.!?]{{0,40}}?to)\s+([0-9][0-9,]*)\b",
            sentence, re.I,
        ):
            month_name, explicit, count_s = m.groups()
            month = MONTHS[month_name.capitalize()]
            _store(out, _resolve_year(month, explicit, publication), month_name, count_s)

        # Current total appears before a reporting month, but only when no 'from/versus'
        # qualifier intervenes. This prevents 2025 totals from being paired with 2024 months.
        for m in re.finditer(
            rf"commercial\s+Chapter\s+11(?:\s+bankruptcy)?\s+filings?\b"
            rf"[^.!?]{{0,100}}?(?:totaled|were|reached|climbed\s+to|rose\s+to|increased[^.!?]{{0,40}}??to|decreased[^.!?]{{0,40}}?to)\s+([0-9][0-9,]*)\b"
            rf"(?:(?!\bfrom\b|\bversus\b)[^.!?]){{0,100}}?\b(?:in|during|for)\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?",
            sentence, re.I,
        ):
            count_s, month_name, explicit = m.groups()
            month = MONTHS[month_name.capitalize()]
            _store(out, _resolve_year(month, explicit, publication), month_name, count_s)

        # Epiq frequently reports a prior-period comparator after 'from', 'versus',
        # 'up/down from', or 'over the'. These are valid exact monthly observations too.
        for m in re.finditer(
            rf"\b(?:from|versus|up\s+from|down\s+from|over\s+the)\s+(?:the\s+)?([0-9][0-9,]*)"
            rf"(?:\s+commercial\s+chapter\s+11(?:\s+bankruptcy)?\s+filings?|\s+filings?)?"
            rf"[^.!?]{{0,60}}?\b(?:in|during|for|recorded\s+in)\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?",
            sentence, re.I,
        ):
            count_s, month_name, explicit = m.groups()
            month = MONTHS[month_name.capitalize()]
            _store(out, _resolve_year(month, explicit, publication), month_name, count_s)

        # Common form: 'in March 2025 ... climbing to 733 from the 611 filings ... in March 2024'.
        for m in re.finditer(
            rf"\b(?:in|during|for)\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?"
            rf"[^.!?]{{0,120}}?(?:climbing\s+to|rose\s+to|increased[^.!?]{{0,40}}?to|decreased[^.!?]{{0,40}}?to|totaled)\s+([0-9][0-9,]*)\b",
            sentence, re.I,
        ):
            month_name, explicit, count_s = m.groups()
            month = MONTHS[month_name.capitalize()]
            _store(out, _resolve_year(month, explicit, publication), month_name, count_s)

    return [(year, month, out[(year, month)]) for year, month in sorted(out)]


def _row(year: int, month: int, value: int, source: str) -> dict:
    return {
        "series_code": "US_COMMERCIAL_CH11",
        "observation_date": f"{year:04d}-{month:02d}-01",
        "value": value,
        "frequency": "M",
        "source": f"EpiqAACER:{source}",
    }


def fetch_epiq_ch11_rows(start: date, end: date, *, max_pages: int = 20) -> list[dict]:
    roots = [
        "https://www.epiqglobal.com/en-us/resource-center/news",
        "https://www.epiqglobal.com/en-hk/resource-center/news",
        "https://www.epiqglobal.com/en-gb/resource-center/news",
    ]
    links: set[str] = set()
    for root in roots:
        empty_pages = 0
        for page in range(1, max_pages + 1):
            url = root if page == 1 else f"{root}?page={page}"
            try:
                response = _request(url)
            except Exception:
                empty_pages += 1
                if empty_pages >= 3:
                    break
                continue
            page_links = {
                urljoin(response.url, href)
                for href in re.findall(r'href=["\']([^"\']+)["\']', response.text, re.I)
                if "/resource-center/news/" in href
                and any(token in href.lower() for token in ("bankrupt", "chapter-11", "chapter-11s", "filing"))
            }
            before = len(links)
            links.update(page_links)
            empty_pages = empty_pages + 1 if len(links) == before else 0
            if empty_pages >= 3:
                break

    values: dict[tuple[int, int], tuple[int, str]] = {}
    first = date(start.year, start.month, 1)
    for url in sorted(links):
        try:
            text = _plain_html(_request(url).text)
        except Exception:
            continue
        for year, month, count in extract_epiq_ch11(text):
            observed = date(year, month, 1)
            if first <= observed <= end:
                values.setdefault((year, month), (count, url))
    return [_row(y, m, count, url) for (y, m), (count, url) in sorted(values.items())]
