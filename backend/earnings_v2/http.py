from __future__ import annotations

import json
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504, 520})
RETRY_TOTAL = 2  # 최초 요청에 재시도 2회를 더해 최대 3회 호출한다.


class ExecutionDeadlineExceeded(TimeoutError):
    """워크플로 외부 강제 종료 전에 애플리케이션이 기록 가능한 실패로 전환한다."""


class ResponseDeadlineExceeded(TimeoutError):
    """바이트가 계속 들어오더라도 응답 전체가 총시간을 넘으면 발생한다."""


def resilient_session() -> requests.Session:
    """Supabase의 읽기 GET만 어댑터 계층에서 제한적으로 재시도한다."""
    retry = Retry(
        total=RETRY_TOTAL,
        connect=RETRY_TOTAL,
        read=RETRY_TOTAL,
        status=RETRY_TOTAL,
        other=0,
        backoff_factor=1,
        status_forcelist=RETRYABLE_STATUS_CODES,
        # RPC는 HTTP POST여도 DB 함수가 비멱등일 수 있다. 응답 유실 뒤
        # 자동 재호출로 상태 카운터나 계산 버전을 중복 변경하지 않는다.
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def provider_session() -> requests.Session:
    """공급자 재시도는 아래 단일 정책에서만 수행해 중첩 재시도를 막는다."""
    return requests.Session()


def _response_bytes(response: Any, deadline: float, monotonic: Any) -> bytes:
    if not hasattr(response, "iter_content"):
        return bytes(getattr(response, "content", b""))
    chunks: list[bytes] = []
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if monotonic() > deadline:
            raise ResponseDeadlineExceeded("response total deadline exceeded")
        if chunk:
            chunks.append(chunk)
    if monotonic() > deadline:
        raise ResponseDeadlineExceeded("response total deadline exceeded")
    return b"".join(chunks)


def _retry_delay(response: Any, attempt: int) -> float:
    headers = getattr(response, "headers", {}) or {}
    retry_after = str(headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return min(float(retry_after), 10.0)
    return min(float(2 ** (attempt - 1)), 4.0)


def bounded_request(
    session: Any,
    method: str,
    url: str,
    *,
    provider: str,
    operation: str,
    total_timeout: float,
    attempt_timeout: float | None,
    connect_timeout: float,
    read_timeout: float,
    binary: bool = False,
    on_retry: Any | None = None,
    monotonic: Any = time.monotonic,
    sleep: Any = time.sleep,
    **kwargs: Any,
) -> Any:
    """연결·읽기·응답 총시간·재시도를 하나의 요청 예산 안에서 관리한다."""
    started = monotonic()
    deadline = started + total_timeout
    last_error: Exception | None = None
    for attempt in range(1, RETRY_TOTAL + 2):
        remaining = deadline - monotonic()
        if remaining <= 0:
            last_error = ResponseDeadlineExceeded("request total deadline exceeded")
            break
        response: Any = None
        try:
            attempt_deadline = min(
                deadline,
                monotonic() + (attempt_timeout if attempt_timeout is not None else remaining),
            )
            attempt_remaining = attempt_deadline - monotonic()
            call = getattr(session, method.lower())
            response = call(
                url,
                timeout=(min(connect_timeout, attempt_remaining), min(read_timeout, attempt_remaining)),
                stream=True,
                **kwargs,
            )
            response.raise_for_status()
            # 단순 테스트 대역은 iter_content가 없으므로 기존 json() 계약을 사용한다.
            if not binary and not hasattr(response, "iter_content"):
                return response.json()
            content = _response_bytes(response, attempt_deadline, monotonic)
            if binary:
                return content
            return json.loads(content.decode("utf-8-sig"))
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is None:
                status = getattr(response, "status_code", None)
            retryable = (
                isinstance(exc, (requests.Timeout, requests.ConnectionError, ResponseDeadlineExceeded))
                or status in RETRYABLE_STATUS_CODES
            )
            if not retryable or attempt > RETRY_TOTAL:
                break
            delay = _retry_delay(response, attempt)
            if monotonic() + delay >= deadline:
                break
            if on_retry is not None:
                on_retry(attempt, safe_request_failure(provider, operation, exc), round(deadline - monotonic(), 3))
            sleep(delay)
    assert last_error is not None
    raise last_error


def safe_request_failure(provider: str, operation: str, error: Exception) -> str:
    """인증정보·URL·응답 본문 없이 최종 전송 실패 종류만 반환한다."""
    response: Any = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None:
        return f"{provider} {operation} returned HTTP {status}"
    if isinstance(error, ExecutionDeadlineExceeded):
        return str(error)
    if isinstance(error, ResponseDeadlineExceeded):
        return f"{provider} {operation} exceeded total response deadline"
    if isinstance(error, requests.ConnectTimeout):
        return f"{provider} {operation} timed out (ConnectTimeout)"
    if isinstance(error, requests.ReadTimeout):
        return f"{provider} {operation} timed out (ReadTimeout)"
    if isinstance(error, requests.Timeout):
        return f"{provider} {operation} timed out ({type(error).__name__})"
    if isinstance(error, requests.ConnectionError):
        return f"{provider} {operation} connection failed ({type(error).__name__})"
    return f"{provider} {operation} request failed ({type(error).__name__})"
