from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
RETRY_TOTAL = 2  # 최초 요청에 재시도 2회를 더해 최대 3회 호출한다.


def resilient_session() -> requests.Session:
    """읽기 전용 공급자 호출과 멱등 RPC의 일시 장애만 제한적으로 재시도한다."""
    retry = Retry(
        total=RETRY_TOTAL,
        connect=RETRY_TOTAL,
        read=RETRY_TOTAL,
        status=RETRY_TOTAL,
        other=0,
        backoff_factor=1,
        status_forcelist=RETRYABLE_STATUS_CODES,
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def safe_request_failure(provider: str, operation: str, error: Exception) -> str:
    """인증정보·URL·응답 본문 없이 최종 전송 실패 종류만 반환한다."""
    response: Any = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None:
        return f"{provider} {operation} returned HTTP {status}"
    if isinstance(error, requests.ConnectTimeout):
        return f"{provider} {operation} timed out (ConnectTimeout)"
    if isinstance(error, requests.ReadTimeout):
        return f"{provider} {operation} timed out (ReadTimeout)"
    if isinstance(error, requests.Timeout):
        return f"{provider} {operation} timed out ({type(error).__name__})"
    if isinstance(error, requests.ConnectionError):
        return f"{provider} {operation} connection failed ({type(error).__name__})"
    return f"{provider} {operation} request failed ({type(error).__name__})"
