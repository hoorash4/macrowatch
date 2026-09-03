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


class InvalidJsonResponse(ValueError):
    """JSON 공급자가 정상 HTTP 상태로 비정상 본문을 반환하면 발생한다."""


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


def _emit_progress(callback: Any | None, event: str, **details: Any) -> None:
    if callback is not None:
        callback(event, details)


def _response_bytes(
    response: Any,
    deadline: float | None,
    monotonic: Any,
    on_progress: Any | None,
    attempt: int,
    started: float,
) -> bytes:
    if not hasattr(response, "iter_content"):
        content = bytes(getattr(response, "content", b""))
        _emit_progress(
            on_progress, "body", attempt=attempt, complete=True,
            elapsed_seconds=round(monotonic() - started, 3),
            bytes_received=len(content), chunk_count=1 if content else 0,
        )
        return content
    chunks: list[bytes] = []
    byte_count = 0
    chunk_count = 0
    complete = False
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if deadline is not None and monotonic() > deadline:
                raise ResponseDeadlineExceeded("response total deadline exceeded")
            if chunk:
                chunks.append(chunk)
                byte_count += len(chunk)
                chunk_count += 1
        if deadline is not None and monotonic() > deadline:
            raise ResponseDeadlineExceeded("response total deadline exceeded")
        complete = True
        return b"".join(chunks)
    finally:
        _emit_progress(
            on_progress, "body", attempt=attempt, complete=complete,
            elapsed_seconds=round(monotonic() - started, 3),
            bytes_received=byte_count, chunk_count=chunk_count,
        )


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
    total_timeout: float | None,
    attempt_timeout: float | None,
    connect_timeout: float,
    read_timeout: float,
    binary: bool = False,
    retry_total: int = RETRY_TOTAL,
    on_retry: Any | None = None,
    on_progress: Any | None = None,
    monotonic: Any = time.monotonic,
    sleep: Any = time.sleep,
    **kwargs: Any,
) -> Any:
    """연결·읽기·선택적 응답 총시간·재시도를 하나의 정책에서 관리한다."""
    retry_limit = max(int(retry_total), 0)
    started = monotonic()
    deadline = started + total_timeout if total_timeout is not None else None
    last_error: Exception | None = None
    for attempt in range(1, retry_limit + 2):
        remaining = deadline - monotonic() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            last_error = ResponseDeadlineExceeded("request total deadline exceeded")
            break
        response: Any = None
        try:
            attempt_remaining = attempt_timeout
            if remaining is not None:
                attempt_remaining = min(remaining, attempt_timeout or remaining)
            connect = min(connect_timeout, attempt_remaining) if attempt_remaining is not None else connect_timeout
            read = min(read_timeout, attempt_remaining) if attempt_remaining is not None else read_timeout
            call = getattr(session, method.lower())
            response = call(
                url,
                timeout=(connect, read),
                stream=True,
                **kwargs,
            )
            headers = getattr(response, "headers", {}) or {}
            _emit_progress(
                on_progress, "headers", attempt=attempt,
                elapsed_seconds=round(monotonic() - started, 3),
                status_code=getattr(response, "status_code", None),
                content_type=headers.get("Content-Type"),
                content_encoding=headers.get("Content-Encoding"),
                content_length=headers.get("Content-Length"),
            )
            response.raise_for_status()
            # 단순 테스트 대역은 iter_content가 없으므로 기존 json() 계약을 사용한다.
            if not binary and not hasattr(response, "iter_content"):
                try:
                    payload = response.json()
                except (ValueError, UnicodeError) as exc:
                    raise InvalidJsonResponse("response body is not valid JSON") from exc
                _emit_progress(
                    on_progress, "body", attempt=attempt, complete=True,
                    elapsed_seconds=round(monotonic() - started, 3),
                    bytes_received=len(bytes(getattr(response, "content", b""))), chunk_count=0,
                )
                return payload
            # attempt_timeout은 연결 및 멈춘 소켓을 재시도하기 위한 대기
            # 상한이다. 응답 바이트가 계속 들어오는 정상 스트림은 요청 전체
            # deadline 안에서 완료되도록 두고 중간에 재시도하지 않는다.
            content = _response_bytes(response, deadline, monotonic, on_progress, attempt, started)
            if binary:
                return content
            try:
                return json.loads(content.decode("utf-8-sig"))
            except (ValueError, UnicodeError) as exc:
                raise InvalidJsonResponse("response body is not valid JSON") from exc
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is None:
                status = getattr(response, "status_code", None)
            retryable = (
                isinstance(exc, (
                    requests.Timeout, requests.ConnectionError,
                    ResponseDeadlineExceeded, InvalidJsonResponse,
                ))
                or status in RETRYABLE_STATUS_CODES
            )
            if not retryable or attempt > retry_limit:
                break
            delay = _retry_delay(response, attempt)
            if deadline is not None and monotonic() + delay >= deadline:
                break
            if on_retry is not None:
                remaining_budget = round(deadline - monotonic(), 3) if deadline is not None else None
                on_retry(attempt, safe_request_failure(provider, operation, exc), remaining_budget)
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
