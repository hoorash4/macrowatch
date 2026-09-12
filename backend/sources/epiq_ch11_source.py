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
        existing = out.get((year, month))
        if existing is not None and existing != count:
            raise RuntimeError(f"Epiq page contains conflicting counts for {year:04d}-{month:02d}: {existing} vs {count}")
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

        for match in re.finditer(
            rf"\bin\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?[^.!?]{{0,160}}?(?:filings?\s+)?(?:climbing|rising|rose|increased|decreased|falling|fell)\s+(?:by\s+[^.!?]{{0,30}}?\s+)?to\s+([0-9][0-9,]*)\b",
            sentence, re.I,
        ):
            _add(out, match.group(3), match.group(1), match.group(2), publication)

        for match in re.finditer(
            rf"\b(?:from|versus|over)\s+(?:the\s+)?([0-9][0-9,]*)\s+(?:commercial\s+chapter\s+11(?:\s+bankruptcy)?\s+)?filings?[^.!?]{{0,80}}?\bin\s+({MONTH_PATTERN})(?:\s+(20\d{{2}}))?",
            sentence, re.I,
        ):
            _add(out, match.group(1), match.group(2), match.group(3), publication)

    return [(year, month, out[(year, month)]) for year, month in sorted(out)]


def _candidate_priority(publication: tuple[int, int] | None, year: int, month: int) -> tuple[int, int]:
    """Prefer the dedicated release published one month after its observation month."""
    if publication is None:
        return (3, 999)
    pub_year, pub_month = publication
    distance = (pub_year * 12 + pub_month) - (year * 12 + month)
    if distance == 1:
        return (0, 0)
    if distance == 0:
        return (1, 0)
    if distance > 1:
        return (2, distance)
    return (3, abs(distance))


def _consider_candidate(
    values: dict[tuple[int, int], tuple[int, str, tuple[int, int]]],
    *,
    year: int,
    month: int,
    count: int,
    url: str,
    publication: tuple[int, int] | None,
) -> None:
    key = (year, month)
    priority = _candidate_priority(publication, year, month)
    current = values.get(key)
    if current is None or priority < current[2]:
        values[key] = (count, url, priority)
        return
    if priority == current[2] and count != current[0]:
        raise RuntimeError(
            f"Epiq has conflicting equally authoritative counts for {year:04d}-{month:02d}: "
            f"{current[0]} ({current[1]}) vs {count} ({url})"
        )


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

    values: dict[tuple[int, int], tuple[int, str, tuple[int, int]]] = {}
    first = date(start.year, start.month, 1)
    for url in sorted(links):
        try:
            text = _plain_html(_request(url).text)
            publication = _publication_date(text)
            extracted = extract_epiq_ch11(text)
        except Exception as error:
            # A contradictory official page must not silently contaminate the series.
            if isinstance(error, RuntimeError):
                raise
            continue
        for year, month, count in extracted:
            observed = date(year, month, 1)
            if first <= observed <= end:
                _consider_candidate(
                    values,
                    year=year,
                    month=month,
                    count=count,
                    url=url,
                    publication=publication,
                )
    return [
        _row(year, month, count, url)
        for (year, month), (count, url, _priority) in sorted(values.items())
    ]
