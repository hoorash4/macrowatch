"""Official KRX KOSPI index PER/PBR reader.

KRX Data Marketplace exposes the index fundamental endpoint MDCSTAT00702.  Recent KRX
sessions may require the same warm-up/login cookie flow used by pykrx, so this adapter keeps
that transport concern outside the economic-chart calculation/storage layer.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Any

import requests

from common import request_with_retry

KRX_DATA_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_LOGIN_PAGE = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
KRX_LOGIN_JSP = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
KRX_LOGIN_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
KRX_INDEX_BLD = "dbms/MDC/STAT/standard/MDCSTAT00702"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SERIES = {
    "KOSPI_PER": ("WT_PER", "D"),
    "KOSPI_PBR": ("WT_STKPRC_NETASST_RTO", "D"),
}


def _numeric(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {".", "-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date_text(value: object) -> str | None:
    raw = str(value or "").strip().replace("/", "").replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _base_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Referer": "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd",
        "X-Requested-With": "XMLHttpRequest",
    }


def _warm_session(session: requests.Session) -> None:
    # Establish the JSESSIONID and the login iframe session before the data POST.
    first = request_with_retry(lambda: session.get(
        KRX_LOGIN_PAGE, headers={"User-Agent": USER_AGENT}, timeout=20
    ))
    first.raise_for_status()
    second = request_with_retry(lambda: session.get(
        KRX_LOGIN_JSP,
        headers={"User-Agent": USER_AGENT, "Referer": KRX_LOGIN_PAGE},
        timeout=20,
    ))
    second.raise_for_status()


def _optional_login(session: requests.Session) -> bool:
    login_id = os.getenv("KRX_ID", "").strip()
    login_pw = os.getenv("KRX_PW", "").strip()
    if not login_id or not login_pw:
        return False
    payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "", "mbrId": login_id, "pw": login_pw}
    response = request_with_retry(lambda: session.post(
        KRX_LOGIN_URL,
        data=payload,
        headers={"User-Agent": USER_AGENT, "Referer": KRX_LOGIN_PAGE},
        timeout=20,
    ))
    response.raise_for_status()
    data = response.json()
    code = str(data.get("_error_code") or "")
    if code == "CD011":
        payload["skipDup"] = "Y"
        response = request_with_retry(lambda: session.post(
            KRX_LOGIN_URL,
            data=payload,
            headers={"User-Agent": USER_AGENT, "Referer": KRX_LOGIN_PAGE},
            timeout=20,
        ))
        response.raise_for_status()
        data = response.json()
        code = str(data.get("_error_code") or "")
    if code != "CD001":
        message = str(data.get("_error_message") or code or "unknown login error")
        raise RuntimeError(f"KRX 로그인 실패: {message}")
    return True


def _payload(session: requests.Session, start: date, end: date) -> list[dict[str, Any]]:
    response = request_with_retry(lambda: session.post(
        KRX_DATA_URL,
        headers=_base_headers(),
        data={
            "bld": KRX_INDEX_BLD,
            "locale": "ko_KR",
            "indTpCd": "1",
            "indTpCd2": "001",  # KOSPI index ticker 1001
            "strtDd": start.strftime("%Y%m%d"),
            "endDd": end.strftime("%Y%m%d"),
        },
        timeout=45,
    ))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("KRX KOSPI PER/PBR 응답 형식이 올바르지 않습니다.")
    error_code = str(data.get("_error_code") or "")
    if error_code and error_code != "CD001":
        raise RuntimeError(f"KRX KOSPI PER/PBR 오류: {data.get('_error_message') or error_code}")
    output = data.get("output")
    if not isinstance(output, list):
        raise RuntimeError("KRX KOSPI PER/PBR output이 없습니다.")
    return [row for row in output if isinstance(row, dict)]


def fetch_krx_kospi_fundamental_rows(start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    """Read KOSPI market-wide PER/PBR from KRX, chunking the official 2-year query window."""
    result = {code: [] for code in SERIES}
    session = requests.Session()
    _warm_session(session)
    _optional_login(session)
    cursor = start
    first = True
    total_source_rows = 0
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=729), end)
        if not first:
            time.sleep(1)
        first = False
        source_rows = _payload(session, cursor, chunk_end)
        total_source_rows += len(source_rows)
        for item in source_rows:
            observed = _date_text(item.get("TRD_DD"))
            if observed is None:
                continue
            for code, (field, frequency) in SERIES.items():
                value = _numeric(item.get(field))
                if value is None:
                    continue
                result[code].append({
                    "series_code": code,
                    "observation_date": observed,
                    "value": value,
                    "frequency": frequency,
                    "source": "KRX_DATA_MARKETPLACE:MDCSTAT00702/KOSPI1001",
                })
        cursor = chunk_end + timedelta(days=1)
    if total_source_rows == 0 and start < end:
        auth_hint = " KRX_ID/KRX_PW secrets를 설정하면 인증 세션으로 재시도할 수 있습니다." if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")) else ""
        raise RuntimeError(f"KRX KOSPI PER/PBR 조회 결과가 비어 있습니다.{auth_hint}")
    return result
