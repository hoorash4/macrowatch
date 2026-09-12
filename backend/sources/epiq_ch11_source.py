"""Public Epiq AACER Commercial Chapter 11 monthly source."""
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
    match = re.search(rf"\b({MONTH_PATTERN}|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{{1,2}},\s+(20\d{{2}})\b", text, re.I)
    if not match:
        return None
    token = match.group(1).capitalize()
    aliases = {"Jan":"January","Feb":"February","Mar":"March","Apr":"April","Jun":"June","Jul":"July","Aug":"August","Sep":"September","Sept":"September","Oct":"October","Nov":"November","Dec":"December"}
    month_name = aliases.get(token, token)
    return int(match.group(2)), MONTHS[month_name]


def _resolved_year(month: int, explicit_year: str | None, publication: tuple[int, int] | None) -> int | None:
    if explicit_year:
        return int(explicit_year)
    if not publication:
        return None
    pub_year, pub_month = publication
    return pub_year if month <= pub_month else pub_year - 1


def _add(out: dict[tuple[int, int], int], count_s: str, month_name: str, year_s: str | None, publication: tuple[int, int] | None) -> None:
    month = MONTHS[month_name.capitalize()]
    year = _resolved_year(month, year_s, publication)
    if year is None:
        return
    count = int(count_s.replace(",", ""))
    if 0 < count < 100000:
        out[(year, month)] = count


def extract_epiq_ch11(text: str) -> list[tuple[int, int, int]]:
    """Extract only count/month pairs explicitly tied together by an Epiq sentence."""
    clean = re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))
    publication = _publication_date(clean)
    out: dict[tuple[int, int], int] = {}
    for sentence in re.split(r"(?<=[.!?])\s+", clean):
        if not re.search(r"commercial\s+chapter\s+11", sentence, re.I):
            continue

        patterns = [
            rf"([0-9][0-9,]*)\s+commercial\s+chapter\s+11(?:\s+bankruptcy)?\s+filings?\s+(?:recorded\s+)?in\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?",
            rf"commercial\s+chapter\s+11(?:\s+bankruptcy)?\s+filings?\s+(?:totaled|were|reached)\s+([0-9][0-9,]*)\s+in\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, sentence, re.I):
                _add(out, match.group(1), match.group(2), match.group(3), publication)

        # Form: "in March 2025 ... filings climbing to 733".
        for match in re.finditer(
            rf"\bin\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?[^.!?]{{0,160}}?(?:filings?\s+)?(?:climbing|rising|rose|increased|decreased|falling|fell)\s+(?:by\s+[^.!?]{{0,30}}?\s+)?to\s+([0-9][0-9,]*)\b",
            sentence, re.I,
        ):
            _add(out, match.group(3), match.group(1), match.group(2), publication)

        # Prior-period comparator: "from/over the 611 filings ... in March 2024".
        for match in re.finditer(
            rf"\b(?:from|versus|over)\s+(?:the\s+)?([0-9][0-9,]*)\s+(?:commercial\s+chapter\s+11(?:\s+bankruptcy)?\s+)?filings?[^.!?]{{0,80}}?\bin\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?",
            sentence, re.I,
        ):
            _add(out, match.group(1), match.group(2), match.group(3), publication)

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
        stale_pages = 0
        for page in range(1, max_pages + 1):
            url = root if page == 1 else f"{root}?page={page}"
            try:
                response = _request(url)
            except Exception:
                stale_pages += 1
                if stale_pages >= 3:
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
            stale_pages = stale_pages + 1 if len(links) == before else 0
            if stale_pages >= 3:
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
                values[(year, month)] = (count, url)
    return [_row(year, month, count, url) for (year, month), (count, url) in sorted(values.items())]
