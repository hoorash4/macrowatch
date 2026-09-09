"""한국 중소기업 위험지수의 월별 원자료를 수집한다."""

from __future__ import annotations

import html
import io
import os
import re
import time
from datetime import date
from typing import Any
from urllib.parse import urljoin

import requests


KOSIS_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
KBIZ_LIST_URL = "https://www.kbiz.or.kr/ko/contents/bbs/list.do"
KBIZ_BASE_URL = "https://www.kbiz.or.kr"
FSS_LIST_URL = "https://www.fss.or.kr/fss/bbs/B0000188/list.do"
FSS_BASE_URL = "https://www.fss.or.kr"
TIMEOUT_SECONDS = 60
KOSIS_START = date(2023, 1, 1)

KOSIS_SERIES = {
    "headline_outlook": {
        "tblId": "DT_D10102",
        "itmId": "1634013103124559T6",
        "objL1": "15340a.a",
    },
    "funding_outlook": {
        "tblId": "DT_D10116",
        "itmId": "1634013103124559T6",
        "objL1": "15340a.a",
    },
    "utilization_sa": {
        "tblId": "DT_D10125",
        "itmId": "1634013103124559T1",
        "objL1": "15340a.b1",
    },
}


def _month_key(raw: object) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 6:
        return None
    year, month = int(digits[:4]), int(digits[4:6])
    if year < 1900 or month not in range(1, 13):
        return None
    return f"{year:04d}-{month:02d}-01"


def parse_kosis_rows(rows: object, start: date, end: date) -> dict[str, float]:
    """KOSIS JSON 결과를 월별 숫자 시계열로 정규화한다."""
    if isinstance(rows, dict):
        message = rows.get("errMsg") or rows.get("message") or rows.get("MESSAGE")
        raise RuntimeError(f"KOSIS API 오류: {message or rows}")
    if not isinstance(rows, list):
        raise RuntimeError("KOSIS API 응답 형식이 올바르지 않습니다.")
    start_key, end_key = start.replace(day=1).isoformat(), end.replace(day=1).isoformat()
    values: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        month = _month_key(row.get("PRD_DE") or row.get("PRD_DE_NM") or row.get("TIME"))
        if month is None or not start_key <= month <= end_key:
            continue
        raw_value = row.get("DT")
        try:
            values[month] = float(str(raw_value).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return values


def fetch_kosis_series(
    name: str,
    start: date,
    end: date,
    *,
    api_key: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, float]:
    """KOSIS OpenAPI에서 지정한 중소기업 월별 시계열을 받는다."""
    config = KOSIS_SERIES[name]
    key = (api_key or os.getenv("KOSIS_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("Missing required environment variable: KOSIS_API_KEY")
    effective_start = max(start.replace(day=1), KOSIS_START)
    if effective_start > end:
        return {}
    params = {
        "method": "getList",
        "apiKey": key,
        "orgId": "340",
        "tblId": config["tblId"],
        "itmId": config["itmId"],
        "objL1": config["objL1"],
        "prdSe": "M",
        "startPrdDe": effective_start.strftime("%Y%m"),
        "endPrdDe": end.strftime("%Y%m"),
        "format": "json",
        "jsonVD": "Y",
    }
    client = session or requests.Session()
    response = client.get(KOSIS_URL, params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    values = parse_kosis_rows(response.json(), effective_start, end)
    if not values:
        raise RuntimeError(f"KOSIS {name} 시계열이 비어 있습니다.")
    return values


def _get_with_retry(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    response = None
    for attempt in range(4):
        response = session.get(url, timeout=TIMEOUT_SECONDS, **kwargs)
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
            break
        time.sleep(2**attempt)
    if response is None:
        raise RuntimeError(f"응답이 없습니다: {url}")
    response.raise_for_status()
    return response


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 실행 환경 설정 오류
        raise RuntimeError("PDF 백필에는 pypdf가 필요합니다.") from exc
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _number_tokens(value: str) -> list[float]:
    return [float(item.replace("△", "-")) for item in re.findall(r"△?\d+(?:\.\d+)?", value)]


def _one_decimal_tokens(value: str) -> list[float]:
    """붙어서 추출된 KBIZ 지수열을 소수 첫째 자리 단위로 분리한다."""
    pattern = r"△?\d{1,3}\.\d(?=△|\d{1,3}\.|[^0-9.]|$)|△?\d+(?=[^0-9.]|$)"
    return [float(item.replace("△", "-")) for item in re.findall(pattern, value)]


def parse_kbiz_report(text: str) -> dict[str, tuple[str, float]]:
    """KBIZ 월간 보고서 한 건에서 전산업 전망·자금사정·계절조정 가동률을 읽는다."""
    compact = re.sub(r"[ \t\u00a0]+", "", text.replace("−", "-").replace("–", "-"))
    title = re.search(r"(20\d{2})[.년]\s*(\d{1,2})월전망", compact)
    if not title:
        raise ValueError("KBIZ 보고서의 전망 기준월을 찾지 못했습니다.")
    forecast_month = f"{int(title.group(1)):04d}-{int(title.group(2)):02d}-01"

    headline_match = re.search(r"중소기업\d{1,2}월업황전망SBHI는([^\nㅇ□]+)", compact)
    if not headline_match:
        headline_match = re.search(r"전산업업황전망SBHI([^\n]+)", compact)
    if not headline_match:
        raise ValueError("KBIZ 보고서의 전산업 업황전망을 찾지 못했습니다.")
    if "전산업업황전망SBHI" in headline_match.group(0):
        tokens = _number_tokens(headline_match.group(1))
        headline = tokens[-3] if len(tokens) >= 3 else tokens[-1]
    else:
        sentence = headline_match.group(1)
        tokens = _number_tokens(sentence)
        if not tokens:
            raise ValueError("KBIZ 보고서의 전산업 업황전망 숫자열이 올바르지 않습니다.")
        headline = tokens[0] if re.match(r"\d", sentence) else tokens[-1]

    funding_section = re.search(r"전산업경기전망항목.*?자금사정([^\n]+)", compact, re.DOTALL)
    if not funding_section:
        raise ValueError("KBIZ 보고서의 자금사정 전망을 찾지 못했습니다.")
    funding_values = _one_decimal_tokens(funding_section.group(1).split("수준판단", 1)[0])
    if len(funding_values) < 3:
        raise ValueError("KBIZ 자금사정 전망 숫자열이 올바르지 않습니다.")
    funding = funding_values[-3]

    utilization_month_match = re.search(r"중소제조업평균가동률\((20\d{2})[.]?(\d{1,2})월\)", compact)
    utilization_row = re.search(
        r"중소제조업\(계절조정\)(.*?)(?:소기업|중기업|일반제조업|<)",
        compact,
        re.DOTALL,
    )
    if not utilization_month_match or not utilization_row:
        raise ValueError("KBIZ 보고서의 계절조정 평균가동률을 찾지 못했습니다.")
    utilization_values = _one_decimal_tokens(utilization_row.group(1))
    if len(utilization_values) < 3:
        raise ValueError("KBIZ 계절조정 가동률 숫자열이 올바르지 않습니다.")
    utilization_month = f"{int(utilization_month_match.group(1)):04d}-{int(utilization_month_match.group(2)):02d}-01"
    return {
        "headline_outlook": (forecast_month, headline),
        "funding_outlook": (forecast_month, funding),
        "utilization_sa": (utilization_month, utilization_values[-3]),
    }


def _kbiz_report_entries(markup: str) -> list[tuple[int, int, int]]:
    pattern = re.compile(
        r"goView\((\d+),[^)]*\)[\s\S]{0,300}?(20\d{2})년\s*(\d{1,2})월\s*중소기업경기전망조사"
    )
    return [(int(seq), int(year), int(month)) for seq, year, month in pattern.findall(markup)]


def _pdf_links(markup: str, base_url: str) -> list[str]:
    links = re.findall(r'href="([^"]*(?:download|fileDown)\.do\?[^"]+)"', markup, flags=re.I)
    named_pdf_links = re.findall(
        r'<a[^>]+href="([^"]*fileDown\.do\?[^"]+)"[^>]*>[\s\S]{0,400}?<span[^>]*class="name"[^>]*>[^<]*\.pdf',
        markup,
        flags=re.I,
    )
    selected = [link for link in links if ".pdf" in html.unescape(link).lower()] + named_pdf_links
    return list(dict.fromkeys(urljoin(base_url, html.unescape(link)) for link in selected))


def _kbiz_hwp_viewer_attachments(markup: str) -> list[tuple[str, str]]:
    """KBIZ 게시물에서 HWP 첨부의 공식 바로보기 인자를 읽는다."""
    attachments: list[tuple[str, str]] = []
    for item in re.findall(r"<li[^>]*>([\s\S]*?)</li>", markup, flags=re.I):
        if ".hwp" not in re.sub(r"<[^>]+>", " ", item).lower():
            continue
        match = re.search(r'data-ds="([^"]+)"\s+data-seq="(\d+)"[^>]*onclick="fileViwer', item, flags=re.I)
        if match:
            attachments.append((html.unescape(match.group(1)), match.group(2)))
    return attachments


def _kbiz_pdf_sources(
    markup: str,
    client: requests.Session,
) -> list[str]:
    """PDF 첨부와 HWP의 KBIZ 공식 PDF 변환본 URL을 반환한다."""
    sources = _pdf_links(markup, KBIZ_BASE_URL)
    for dataset, sequence in _kbiz_hwp_viewer_attachments(markup):
        metadata = _get_with_retry(
            client,
            urljoin(KBIZ_BASE_URL, "/file_viwer_json.do"),
            params={"ds": dataset, "fleSeq": sequence, "fleSeq2": "", "copyYn": ""},
        ).json()
        viewer_url = str(metadata.get("vwrUrl") or "")
        if metadata.get("resultCode") != 0 or not viewer_url:
            continue
        viewer_markup = _get_with_retry(client, viewer_url).text
        ckey_match = re.search(r'"ckey"\s*:\s*"([^"]+)"', viewer_markup)
        if ckey_match:
            sources.append(urljoin(viewer_url, f"../getFile/{ckey_match.group(1)}/pdf"))
    return list(dict.fromkeys(sources))


def fetch_kbiz_historical(start: date, end: date) -> dict[str, dict[str, float]]:
    """KOSIS 수록 전 구간은 KBIZ 원문 PDF에서 다시 구성한다."""
    result = {name: {} for name in KOSIS_SERIES}
    if start >= KOSIS_START:
        return result
    client = requests.Session()
    client.headers["User-Agent"] = "MacroWatch data collector (public statistics)"
    entries: dict[int, tuple[int, int]] = {}
    for page in range(1, 5):
        response = _get_with_retry(client, KBIZ_LIST_URL, params={"mnSeq": 324, "pg": page, "pgSz": 100})
        page_entries = _kbiz_report_entries(response.text)
        if not page_entries:
            break
        for seq, year, month in page_entries:
            report_month = date(year, month, 1)
            if start.replace(day=1) <= report_month <= end.replace(day=1) and report_month < KOSIS_START:
                entries[seq] = (year, month)
    for seq, _ in sorted(entries.items(), key=lambda item: item[1]):
        view = _get_with_retry(client, urljoin(KBIZ_BASE_URL, f"/ko/contents/bbs/view.do?mnSeq=324&seq={seq}"))
        links = _kbiz_pdf_sources(view.text, client)
        if not links:
            continue
        report = None
        for link in links:
            try:
                content = _get_with_retry(client, link).content
                report = parse_kbiz_report(_pdf_text(content))
                break
            except (ValueError, RuntimeError):
                continue
        if not report:
            continue
        for name, (month, value) in report.items():
            if start.replace(day=1).isoformat() <= month <= end.replace(day=1).isoformat():
                result[name][month] = value
    return result


def parse_fss_report(text: str) -> tuple[str, float]:
    """금감원 원화대출 연체율 자료에서 기준월과 중소법인 연체율을 읽는다."""
    compact = re.sub(r"[ \t\u00a0]+", "", text)
    month_match = re.search(r"[’'`]?(\d{2,4})[.]?(\d{1,2})월말(?:기준)?국내은행", compact)
    if not month_match:
        month_match = re.search(r"(20\d{2})년(\d{1,2})월말", compact)
    if not month_match:
        raise ValueError("금감원 자료의 기준월을 찾지 못했습니다.")
    year = int(month_match.group(1))
    if year < 100:
        year += 2000
    month = int(month_match.group(2))
    value_match = re.search(r"중소법인(?:대출)?연체율(?:은|이)?[\(（]?([0-9]+(?:\.[0-9]+)?)%", compact)
    if not value_match:
        raise ValueError("금감원 자료의 중소법인 연체율을 찾지 못했습니다.")
    return f"{year:04d}-{month:02d}-01", float(value_match.group(1))


def _fss_view_links(markup: str) -> list[str]:
    ids = dict.fromkeys(re.findall(r"view\.do\?[^\"']*nttId=(\d+)", html.unescape(markup)))
    return [f"{FSS_BASE_URL}/fss/bbs/B0000188/view.do?nttId={item}&menuNo=200218" for item in ids]


def _fss_listing_reports(markup: str) -> list[tuple[str, str]]:
    """검색 목록의 각 행에서 기준월 문자열과 PDF 링크를 짝짓는다."""
    reports: list[tuple[str, str]] = []
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", markup, flags=re.I):
        title_match = re.search(r"국내은행의\s*원화대출\s*연체율", re.sub(r"<[^>]+>", " ", row))
        if not title_match:
            continue
        month_match = re.search(r"['’`]?(\d{2,4})[.]?(\d{1,2})월말", re.sub(r"<[^>]+>", "", row))
        pdf_links = _pdf_links(row, FSS_BASE_URL)
        if not month_match or not pdf_links:
            continue
        year = int(month_match.group(1))
        if year < 100:
            year += 2000
        reports.append((f"{year:04d}-{int(month_match.group(2)):02d}-01", pdf_links[0]))
    return reports


def fetch_fss_delinquency(start: date, end: date) -> dict[str, float]:
    """금감원 월별 보도자료에서 국내은행 중소법인 연체율을 구성한다."""
    client = requests.Session()
    client.headers["User-Agent"] = "MacroWatch data collector (public statistics)"
    values: dict[str, float] = {}
    seen: set[str] = set()
    start_key, end_key = start.replace(day=1).isoformat(), end.replace(day=1).isoformat()
    for page in range(1, 25):
        listing = _get_with_retry(
            client,
            FSS_LIST_URL,
            params={
                "menuNo": "200218",
                "pageIndex": page,
                "searchCnd": "1",
                "searchWrd": "원화대출 연체율",
            },
        )
        reports = [(month, link) for month, link in _fss_listing_reports(listing.text) if link not in seen]
        if not reports:
            break
        seen.update(link for _, link in reports)
        for listed_month, pdf_link in reports:
            if listed_month < start_key or listed_month > end_key:
                continue
            try:
                month, value = parse_fss_report(_pdf_text(_get_with_retry(client, pdf_link).content))
            except (ValueError, RuntimeError, requests.RequestException):
                continue
            if start_key <= month <= end_key:
                values[month] = value
        if min(month for month, _ in reports) < start_key:
            break
    if not values:
        raise RuntimeError("금감원 중소법인 연체율 시계열을 찾지 못했습니다.")
    return values


def fetch_all(start: date, end: date, *, include_historical: bool) -> dict[str, dict[str, float]]:
    """세 구성요소와 비교지표를 원자료 출처별로 병합한다."""
    result = {name: {} for name in KOSIS_SERIES}
    if include_historical:
        historical = fetch_kbiz_historical(start, end)
        for name in result:
            result[name].update(historical[name])
    for name in result:
        result[name].update(fetch_kosis_series(name, start, end))
    result["delinquency"] = fetch_fss_delinquency(start, end)
    return result
