"""Official KCS 1-10, 1-20 and month-end export snapshots.

The source is the Korea Customs Service press-release board.  KCS publishes cumulative
export values and cumulative working days.  MacroWatch preserves those raw snapshots and
derives independent workday-adjusted 1-10, 11-20 and 21-month-end segments.
"""
from __future__ import annotations

import calendar
import html
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

from common import request_with_retry

BOARD_URL = "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do"
DETAIL_URL = "https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do"
BBS_ID = "1362"
MENU_ID = "2891"
USER_AGENT = "Mozilla/5.0 MacroWatch/1.0"


@dataclass(frozen=True)
class ReleaseLink:
    title: str
    ntt_sn: str
    ntt_url: str
    stage: str
    reference_month: date
    period_end: date
    published_on: date | None


@dataclass(frozen=True)
class ExportSnapshot:
    stage: str
    reference_month: date
    period_end: date
    cumulative_export_musd: float
    cumulative_workdays: float
    published_on: date | None
    source_url: str


def _clean(markup: str) -> str:
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", markup, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _attr(markup: str, name: str) -> str:
    match = re.search(rf'\b{name}=["\']([^"\']*)["\']', markup, flags=re.I)
    return html.unescape(match.group(1)).strip() if match else ""


def release_identity(title: str) -> tuple[str, date, date] | None:
    normalized = re.sub(r"\s+", " ", title).strip()
    match = re.search(r"(?P<year>20\d{2})년\s*(?P<month>\d{1,2})월", normalized)
    if not match or "수출입" not in normalized or "현황" not in normalized:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    if not 1 <= month <= 12:
        return None
    ref = date(year, month, 1)
    tail = normalized[match.end():]
    if re.search(r"1일\s*[~∼～\-]\s*(?:\d{1,2}월\s*)?10일", tail):
        return "d10", ref, date(year, month, 10)
    if re.search(r"1일\s*[~∼～\-]\s*(?:\d{1,2}월\s*)?20일", tail):
        return "d20", ref, date(year, month, 20)
    if "수출입" in tail and "현황" in tail and not re.search(r"1일\s*[~∼～\-]", tail):
        last_day = calendar.monthrange(year, month)[1]
        return "month_end", ref, date(year, month, last_day)
    return None


def _parse_published_on(row_markup: str) -> date | None:
    matches = re.findall(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", _clean(row_markup))
    if not matches:
        return None
    y, m, d = matches[-1]
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _post_board(session: requests.Session, page: int) -> str:
    response = request_with_retry(lambda: session.post(
        BOARD_URL,
        data={
            "bbsId": BBS_ID,
            "mi": MENU_ID,
            "currPage": str(page),
            "listCo": "50",
            "searchType": "all",
            "searchValue": "수출입 현황",
        },
        timeout=30,
    ))
    response.raise_for_status()
    return response.text


def _total_pages(markup: str) -> int | None:
    match = re.search(r"<span>\s*([0-9,]+)\s*</span>건\s*<span>\s*(\d+)\s*/\s*(\d+)\s*</span>", markup, flags=re.S)
    return int(match.group(3)) if match else None


def _release_links(markup: str) -> list[ReleaseLink]:
    links: list[ReleaseLink] = []
    for row in re.findall(r"<tr\b[^>]*>.*?</tr>", markup, flags=re.I | re.S):
        anchor_match = re.search(r'<a\b[^>]*class=["\'][^"\']*nttInfoBtn[^"\']*["\'][^>]*>.*?</a>', row, flags=re.I | re.S)
        if not anchor_match:
            continue
        anchor = anchor_match.group(0)
        title = _attr(anchor, "title") or _clean(anchor)
        identity = release_identity(title)
        if identity is None:
            continue
        ntt_sn = _attr(anchor, "data-id")
        ntt_url = _attr(anchor, "data-url")
        if not ntt_sn:
            continue
        stage, reference_month, period_end = identity
        links.append(ReleaseLink(
            title=title,
            ntt_sn=ntt_sn,
            ntt_url=ntt_url,
            stage=stage,
            reference_month=reference_month,
            period_end=period_end,
            published_on=_parse_published_on(row),
        ))
    return links


def fetch_release_links(start_month: date, *, max_pages: int = 80) -> tuple[list[ReleaseLink], list[str]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    results: dict[tuple[date, str], ReleaseLink] = {}
    errors: list[str] = []
    page = 1
    pages: int | None = None
    while page <= max_pages and (pages is None or page <= pages):
        try:
            markup = _post_board(session, page)
        except Exception as error:
            errors.append(f"board page {page}: {error.__class__.__name__}: {error}")
            page += 1
            continue
        if pages is None:
            pages = _total_pages(markup)
            if pages is not None and pages > max_pages:
                errors.append(f"KCS filtered board unexpectedly has {pages} pages")
                break
        page_links = _release_links(markup)
        for link in page_links:
            if link.reference_month >= start_month:
                results[(link.reference_month, link.stage)] = link
        if page_links and min(link.reference_month for link in page_links) < start_month:
            break
        if not page_links and page > 3:
            break
        page += 1
        time.sleep(0.15)
    return sorted(results.values(), key=lambda item: (item.reference_month, item.period_end)), errors


def _get_detail(session: requests.Session, link: ReleaseLink) -> str:
    params = {"bbsId": BBS_ID, "mi": MENU_ID, "nttSn": link.ntt_sn}
    if link.ntt_url:
        params["nttSnUrl"] = link.ntt_url
    response = request_with_retry(lambda: session.get(DETAIL_URL, params=params, timeout=30))
    response.raise_for_status()
    return response.text


def _metric_tail(page_text: str, label: str, max_chars: int = 260) -> str:
    match = re.search(rf"{label}.{{0,{max_chars}}}", page_text, flags=re.I)
    return match.group(0) if match else ""


def _bracketed_metric(page_text: str, label: str) -> str:
    match = re.search(rf"{label}\s*[\[［]([^\]］]+)[\]］]", page_text, flags=re.I)
    return match.group(1) if match else ""


def _workdays(page_text: str) -> float | None:
    # Prefer the explicit KCS bracket because the following prose can contain
    # unrelated day counts. Older pages without brackets fall back to a short tail.
    scope = _bracketed_metric(page_text, r"조업\s*일수") or _metric_tail(page_text, r"조업\s*일수", 120)
    if not scope:
        return None
    values = re.findall(r"\)\s*([0-9]+(?:\.[0-9]+)?)\s*일", scope)
    if not values:
        values = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*일", scope)
    return float(values[-1]) if values else None


def _reported_daily_average(page_text: str) -> float | None:
    # Keep parsing inside the '일평균 수출액[...]' payload. A broad tail can run
    # into the next table label '수출(전년동기대비)' and misread its leading value.
    scope = _bracketed_metric(page_text, r"일평균\s*수출액") or _metric_tail(page_text, r"일평균\s*수출액", 100)
    if not scope:
        return None
    values = re.findall(r"[’'‘]?\d{2}\)\s*([0-9]+(?:\.[0-9]+)?)", scope)
    if not values:
        values = re.findall(r"\)\s*([0-9]+(?:\.[0-9]+)?)", scope)
    return float(values[-1]) if values else None


def _export_amount_candidates(markup: str) -> list[float]:
    best: list[float] = []
    for row in re.findall(r"<tr\b[^>]*>.*?</tr>", markup, flags=re.I | re.S):
        row_text = re.sub(r"\s+", "", _clean(row))
        if not row_text.startswith("수출") and "수출(전년동기대비" not in row_text:
            continue
        cells = re.findall(r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>", row, flags=re.I | re.S)
        values: list[float] = []
        for cell in cells:
            cell_text = _clean(cell).strip()
            match = re.match(r"^([0-9][0-9,]*(?:\.[0-9]+)?)", cell_text)
            if not match:
                continue
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if value >= 100:
                values.append(value)
        if len(values) > len(best):
            best = values
    return best


def _fallback_export_amount(candidates: list[float]) -> float:
    if len(candidates) >= 5:
        return candidates[3]
    if len(candidates) >= 3:
        return candidates[-2]
    return candidates[-1]


def parse_snapshot(markup: str, link: ReleaseLink) -> ExportSnapshot:
    page_text = _clean(markup)
    candidates = _export_amount_candidates(markup)
    if not candidates:
        raise ValueError("KCS export amount row not found")
    reported_avg = _reported_daily_average(page_text)
    workdays = _workdays(page_text)

    # Some older KCS HTML revisions expose the reported daily average but the
    # working-day footnote is flattened differently. In that case infer the
    # half-day working-day count from the same official amount/average pair.
    fallback_amount = _fallback_export_amount(candidates)
    if (workdays is None or workdays <= 0) and reported_avg is not None and reported_avg > 0:
        inferred = fallback_amount / (reported_avg * 100.0)
        rounded = round(inferred * 2.0) / 2.0
        if rounded > 0 and abs(inferred - rounded) <= 0.15:
            workdays = rounded
    if workdays is None or workdays <= 0:
        raise ValueError("KCS working days not found")

    if reported_avg is not None:
        expected = reported_avg * workdays * 100.0
        amount = min(candidates, key=lambda value: abs(value - expected))
        if expected > 0 and abs(amount - expected) / expected > 0.08:
            raise ValueError("KCS export amount does not match reported daily average")
    else:
        amount = fallback_amount
    source_url = f"{DETAIL_URL}?bbsId={BBS_ID}&mi={MENU_ID}&nttSn={link.ntt_sn}"
    if link.ntt_url:
        source_url += f"&nttSnUrl={link.ntt_url}"
    return ExportSnapshot(
        stage=link.stage,
        reference_month=link.reference_month,
        period_end=link.period_end,
        cumulative_export_musd=amount,
        cumulative_workdays=workdays,
        published_on=link.published_on,
        source_url=source_url,
    )


def fetch_snapshots(start_month: date, *, max_pages: int = 80) -> tuple[list[ExportSnapshot], list[str]]:
    links, errors = fetch_release_links(start_month, max_pages=max_pages)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    snapshots: list[ExportSnapshot] = []
    for index, link in enumerate(links):
        try:
            snapshots.append(parse_snapshot(_get_detail(session, link), link))
        except Exception as error:
            errors.append(f"{link.title}: {error.__class__.__name__}: {error}")
        if index + 1 < len(links):
            time.sleep(0.12)
    return snapshots, errors


def independent_segment_rows(snapshots: list[ExportSnapshot]) -> list[dict[str, Any]]:
    grouped: dict[date, dict[str, ExportSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.reference_month, {})[snapshot.stage] = snapshot
    rows: list[dict[str, Any]] = []
    for reference_month in sorted(grouped):
        stages = grouped[reference_month]
        d10 = stages.get("d10")
        d20 = stages.get("d20")
        month = stages.get("month_end")
        if d10 and d10.cumulative_workdays > 0:
            rows.append({
                "series_code": "KR_EXPORT_DAILY_AVG",
                "observation_date": d10.period_end.isoformat(),
                "value": round(d10.cumulative_export_musd / d10.cumulative_workdays / 100.0, 6),
                "frequency": "T",
                "source": "KCS:INTRAMONTH_EXPORT/d10",
            })
        if d10 and d20:
            days = d20.cumulative_workdays - d10.cumulative_workdays
            amount = d20.cumulative_export_musd - d10.cumulative_export_musd
            if days > 0 and amount >= 0:
                rows.append({
                    "series_code": "KR_EXPORT_DAILY_AVG",
                    "observation_date": d20.period_end.isoformat(),
                    "value": round(amount / days / 100.0, 6),
                    "frequency": "T",
                    "source": "KCS:INTRAMONTH_EXPORT/d11_20",
                })
        if d20 and month:
            days = month.cumulative_workdays - d20.cumulative_workdays
            amount = month.cumulative_export_musd - d20.cumulative_export_musd
            if days > 0 and amount >= 0:
                rows.append({
                    "series_code": "KR_EXPORT_DAILY_AVG",
                    "observation_date": month.period_end.isoformat(),
                    "value": round(amount / days / 100.0, 6),
                    "frequency": "T",
                    "source": "KCS:INTRAMONTH_EXPORT/d21_end",
                })
    return rows
