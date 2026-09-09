"""한국 중소기업 위험지수의 월별 원자료를 수집한다."""

from __future__ import annotations

import html
import io
import os
import re
import struct
import time
import zlib
from datetime import date
from typing import Any
from urllib.parse import quote, urljoin

import requests


KOSIS_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
KBIZ_LIST_URL = "https://www.kbiz.or.kr/ko/contents/bbs/list.do"
KBIZ_SEARCH_URL = "https://www.kbiz.or.kr/ko/total_search/search.do"
KBIZ_BASE_URL = "https://www.kbiz.or.kr"
ECOS_BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
TIMEOUT_SECONDS = 60
# 자금사정 KOSIS 시계열은 2023년 2월부터 시작하므로 1월까지는 KBIZ 원문을 쓴다.
KOSIS_START = date(2023, 2, 1)

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
    response = _get_with_retry(client, KOSIS_URL, params=params)
    values = parse_kosis_rows(response.json(), effective_start, end)
    if not values:
        raise RuntimeError(f"KOSIS {name} 시계열이 비어 있습니다.")
    return values


def _get_with_retry(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    response = None
    last_error: requests.RequestException | None = None
    for attempt in range(4):
        try:
            response = session.get(url, timeout=TIMEOUT_SECONDS, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 3:
                raise
            time.sleep(2**attempt)
            continue
        if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
            break
        time.sleep(2**attempt)
    if response is None:
        raise RuntimeError(f"응답이 없습니다: {url}") from last_error
    response.raise_for_status()
    return response


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 실행 환경 설정 오류
        raise RuntimeError("PDF 백필에는 pypdf가 필요합니다.") from exc
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _hwp_text(content: bytes) -> str:
    """HWP v5 본문 레코드에서 문단 텍스트를 읽는다."""
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - 실행 환경 설정 오류
        raise RuntimeError("HWP 백필에는 olefile이 필요합니다.") from exc
    document = olefile.OleFileIO(io.BytesIO(content))
    header = document.openstream("FileHeader").read()
    compressed = bool(struct.unpack_from("<I", header, 36)[0] & 1)
    sections = sorted(
        (path for path in document.listdir() if len(path) == 2 and path[0] == "BodyText"),
        key=lambda path: int(re.sub(r"\D", "", path[1]) or 0),
    )
    paragraphs: list[str] = []
    for path in sections:
        body = document.openstream(path).read()
        if compressed:
            body = zlib.decompress(body, -15)
        offset = 0
        while offset + 4 <= len(body):
            record = struct.unpack_from("<I", body, offset)[0]
            offset += 4
            tag_id, size = record & 0x3FF, record >> 20
            if size == 0xFFF:
                if offset + 4 > len(body):
                    break
                size = struct.unpack_from("<I", body, offset)[0]
                offset += 4
            payload = body[offset:offset + size]
            offset += size
            if tag_id == 67:
                text = payload.decode("utf-16le", errors="ignore")
                paragraphs.append(re.sub(r"[\x00-\x1f]", " ", text))
    return "\n".join(paragraphs)


def _number_tokens(value: str) -> list[float]:
    return [float(item.replace("△", "-")) for item in re.findall(r"△?\d+(?:\.\d+)?", value)]


def _one_decimal_tokens(value: str) -> list[float]:
    """붙어서 추출된 KBIZ 지수열을 소수 첫째 자리 단위로 분리한다."""
    pattern = r"△?\d{1,3}\.\d(?=△|\d{1,3}\.|[^0-9.]|$)|△?\d+(?=[^0-9.]|$)"
    return [float(item.replace("△", "-")) for item in re.findall(pattern, value)]


def parse_kbiz_report(text: str, *, require_utilization: bool = True) -> dict[str, tuple[str, float]]:
    """KBIZ 월간 보고서 한 건에서 전산업 전망·자금사정·계절조정 가동률을 읽는다."""
    compact = re.sub(r"[ \t\u00a0]+", "", text.replace("−", "-").replace("–", "-"))
    title = re.search(r"(20\d{2})[.년]\s*(\d{1,2})월전망", compact)
    if not title:
        title = re.search(r"(20\d{2})년(\d{1,2})월중소기업경기전망조사", compact)
    if not title:
        raise ValueError("KBIZ 보고서의 전망 기준월을 찾지 못했습니다.")
    forecast_month = f"{int(title.group(1)):04d}-{int(title.group(2)):02d}-01"

    headline_match = re.search(r"중소기업\d{1,2}월업황전망SBHI는([^\nㅇ□]+)", compact)
    if not headline_match:
        headline_match = re.search(r"전산업업황전망SBHI([^\n]+)", compact)
    if not headline_match:
        headline_summary = re.search(
            r"업황전망경기전망지수\(SBHI[^)]*\)는.*?(\d+(?:\.\d+)?)로\d+개월",
            compact,
        )
        if not headline_summary:
            raise ValueError("KBIZ 보고서의 전산업 업황전망을 찾지 못했습니다.")
        headline = float(headline_summary.group(1))
    elif "전산업업황전망SBHI" in headline_match.group(0):
        tokens = _number_tokens(headline_match.group(1))
        headline = tokens[-3] if len(tokens) >= 3 else tokens[-1]
    else:
        sentence = headline_match.group(1)
        tokens = _number_tokens(sentence)
        if not tokens:
            raise ValueError("KBIZ 보고서의 전산업 업황전망 숫자열이 올바르지 않습니다.")
        headline = tokens[0] if re.match(r"\d", sentence) else tokens[-1]

    funding_section = re.search(r"전산업경기전망항목.*?자금사정([^\n]+)", compact, re.DOTALL)
    if funding_section:
        funding_values = _one_decimal_tokens(funding_section.group(1).split("수준판단", 1)[0])
        if len(funding_values) < 3:
            raise ValueError("KBIZ 자금사정 전망 숫자열이 올바르지 않습니다.")
        funding = funding_values[-3]
    else:
        funding_summary = re.search(r"자금사정\([^)]*?→(\d+(?:\.\d+)?)\)", compact)
        if not funding_summary:
            raise ValueError("KBIZ 보고서의 자금사정 전망을 찾지 못했습니다.")
        funding = float(funding_summary.group(1))

    utilization_month_match = re.search(r"중소제조업평균가동률\((20\d{2})[.]?(\d{1,2})월\)", compact)
    utilization_row = re.search(
        r"중소제조업\(계절조정\)(.*?)(?:소기업|중기업|일반제조업|<)",
        compact,
        re.DOTALL,
    )
    if (not utilization_month_match or not utilization_row) and not require_utilization:
        return {
            "headline_outlook": (forecast_month, headline),
            "funding_outlook": (forecast_month, funding),
        }
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


def _hwp_links(markup: str, base_url: str) -> list[str]:
    links = re.findall(r'href="([^\"]*(?:download|fileDown)\.do\?[^\"]+)"', markup, flags=re.I)
    return list(dict.fromkeys(
        urljoin(base_url, html.unescape(link))
        for link in links
        if ".hwp" in html.unescape(link).lower()
    ))


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


def _kbiz_search_view_links(markup: str) -> list[str]:
    links = re.findall(r'href="([^\"]*/ko/contents/bbs/view\.do\?[^\"]+)"', markup, flags=re.I)
    return list(dict.fromkeys(urljoin(KBIZ_BASE_URL, html.unescape(link)) for link in links))


def _fetch_kbiz_month_from_search(
    target: date,
    client: requests.Session,
) -> dict[str, tuple[str, float]] | None:
    """조사보고서 게시판에 빠진 월은 KBIZ 통합검색의 공식 보도자료에서 찾는다."""
    query = f"{target.year}년 {target.month}월 중소기업경기전망조사"
    search = _get_with_retry(client, KBIZ_SEARCH_URL, params={"schTxt": query})
    target_key = target.isoformat()
    for source in _hwp_links(search.text, KBIZ_BASE_URL):
        try:
            report = parse_kbiz_report(
                _hwp_text(_get_with_retry(client, source).content),
                require_utilization=False,
            )
        except (ValueError, RuntimeError, requests.RequestException):
            continue
        if report.get("funding_outlook", (None, None))[0] == target_key:
            return report
    for source in _kbiz_pdf_sources(search.text, client):
        try:
            report = parse_kbiz_report(
                _pdf_text(_get_with_retry(client, source).content),
                require_utilization=False,
            )
        except (ValueError, RuntimeError, requests.RequestException):
            continue
        if report.get("funding_outlook", (None, None))[0] == target_key:
            return report
    for view_url in _kbiz_search_view_links(search.text):
        view = _get_with_retry(client, view_url)
        for source in _kbiz_pdf_sources(view.text, client):
            try:
                report = parse_kbiz_report(
                    _pdf_text(_get_with_retry(client, source).content),
                    require_utilization=False,
                )
            except (ValueError, RuntimeError, requests.RequestException):
                continue
            if report.get("funding_outlook", (None, None))[0] == target_key:
                return report
    return None


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
    historical_end = min(end.replace(day=1), date(KOSIS_START.year, KOSIS_START.month - 1, 1))
    cursor = start.replace(day=1)
    while cursor <= historical_end:
        key = cursor.isoformat()
        if key not in result["funding_outlook"] or key not in result["headline_outlook"]:
            fallback = _fetch_kbiz_month_from_search(cursor, client)
            if fallback:
                for name, (month, value) in fallback.items():
                    if start.replace(day=1).isoformat() <= month <= end.replace(day=1).isoformat():
                        result[name][month] = value
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return result


def parse_ecos_delinquency_rows(rows: object, start: date, end: date) -> dict[str, float]:
    """ECOS 전국 중소기업대출 1개월 이상 연체율을 월별 값으로 정규화한다."""
    if not isinstance(rows, list):
        raise RuntimeError("ECOS 중소기업대출 연체율 응답 형식이 올바르지 않습니다.")
    start_key, end_key = start.replace(day=1).isoformat(), end.replace(day=1).isoformat()
    values: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        month = _month_key(row.get("TIME"))
        if month is None or not start_key <= month <= end_key:
            continue
        try:
            values[month] = float(str(row.get("DATA_VALUE")).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return values


def fetch_ecos_sme_delinquency(
    start: date,
    end: date,
    *,
    api_key: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, float]:
    """ECOS 141Y005의 전국 중소기업대출 연체율(전체1M)을 조회한다."""
    key = (api_key or os.getenv("ECOS_API_KEY", "")).strip()
    if not key:
        raise RuntimeError("Missing required environment variable: ECOS_API_KEY")
    effective_start = max(start.replace(day=1), date(2019, 12, 1))
    if effective_start > end:
        return {}
    url = "/".join([
        ECOS_BASE_URL,
        quote(key, safe=""),
        "json", "kr", "1", "1000", "141Y005", "M",
        effective_start.strftime("%Y%m"), end.strftime("%Y%m"), "R4AB12", "X00",
    ])
    response = _get_with_retry(session or requests.Session(), url)
    payload = response.json()
    if isinstance(payload, dict) and payload.get("RESULT"):
        raise RuntimeError(f"ECOS API 오류: {payload['RESULT'].get('MESSAGE') or payload['RESULT']}")
    rows = payload.get("StatisticSearch", {}).get("row", []) if isinstance(payload, dict) else []
    values = parse_ecos_delinquency_rows(rows, effective_start, end)
    if not values:
        raise RuntimeError("ECOS 전국 중소기업대출 연체율 시계열이 비어 있습니다.")
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
    result["delinquency"] = fetch_ecos_sme_delinquency(start, end)
    return result
