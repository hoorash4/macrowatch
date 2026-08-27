"""MacroWatch 백엔드 작업이 공유하는 작은 기반 기능.

각 실행 파일은 데이터 수집과 지수 계산이라는 고유 책임만 갖도록 하고,
환경변수·HTTP·Supabase·카카오처럼 모든 작업에서 같은 처리는 이곳에서 관리한다.
이 모듈은 실행 순서나 지수 산식을 결정하지 않는다.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from datetime import date
from typing import Any

import requests


DEFAULT_HTTP_TIMEOUT = 45
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
MACROWATCH_URL = "https://hoorash4.github.io/macrowatch/"


FRED_HTTP_SESSION: requests.Session | None = None
TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


def default_fred_session() -> requests.Session:
    """FRED 호출 사이에 HTTP 연결을 재사용하는 지연 생성 세션을 반환한다."""
    global FRED_HTTP_SESSION
    if FRED_HTTP_SESSION is None:
        FRED_HTTP_SESSION = requests.Session()
    return FRED_HTTP_SESSION


def require_env(name: str) -> str:
    """필수 환경변수를 공백 제거 후 반환한다."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fetch_fred_observations(
    series_id: str,
    api_key: str,
    *,
    start: str | None = None,
    end: str | None = None,
    sort_order: str | None = None,
    limit: int = 100_000,
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """FRED observations 응답을 동일한 검증 방식으로 가져온다."""
    params: dict[str, str | int] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": limit,
    }
    if start:
        params["observation_start"] = start
    if end:
        params["observation_end"] = end
    if sort_order:
        params["sort_order"] = sort_order
    # 기본 호출은 연결 풀을 공유해 여러 FRED 시계열 수집의 TLS 연결 비용을 줄인다.
    # 테스트나 특수 호출은 기존처럼 주입된 session을 우선한다.
    client = session or default_fred_session()
    response = None
    for attempt in range(4):
        response = client.get(FRED_OBSERVATIONS_URL, params=params, headers=headers, timeout=timeout)
        status_code = getattr(response, "status_code", None)
        if status_code not in TRANSIENT_HTTP_STATUSES or attempt == 3:
            break
        retry_after = getattr(response, "headers", {}).get("Retry-After")
        try:
            delay = max(float(retry_after), 0.0) if retry_after else float(2 ** attempt)
        except (TypeError, ValueError):
            delay = float(2 ** attempt)
        time.sleep(min(delay, 30.0))
    if response is None:
        raise RuntimeError("FRED request did not produce a response")
    response.raise_for_status()
    observations = response.json().get("observations", [])
    return observations if isinstance(observations, list) else []


def carry_forward(values: dict[str, float], periods: Iterable[str]) -> dict[str, float]:
    """새 관측값이 없는 기간에는 마지막 실제 관측값을 이어 쓴다."""
    carried: dict[str, float] = {}
    previous: float | None = None
    for period in periods:
        if period in values:
            previous = values[period]
        if previous is not None:
            carried[period] = previous
    return carried


def uncapped_score(value: float, floor: float, reference: float) -> float:
    """고정 기준 구간을 점수화하되 위기 수치가 100을 넘는 것을 허용한다."""
    if reference == floor:
        raise ValueError("Score reference and floor must differ.")
    return max(0.0, (value - floor) / (reference - floor) * 100.0)


def month_start_months_ago(reference: date, months: int) -> date:
    """reference가 속한 달에서 months개월 전의 월초를 반환한다."""
    month_index = reference.year * 12 + reference.month - 1 - months
    return date(month_index // 12, month_index % 12 + 1, 1)


class SupabaseRest:
    """서비스 역할 키로 Supabase REST 테이블을 호출하는 공통 클라이언트."""

    def __init__(
        self,
        *,
        url: str | None = None,
        service_key: str | None = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self.url = (url or require_env("SUPABASE_URL")).rstrip("/")
        self.key = service_key or require_env("SUPABASE_SERVICE_ROLE_KEY")
        self.timeout = timeout
        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        self.session = requests.Session()

    def request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        response = self.session.request(
            method,
            f"{self.url}/rest/v1/{table}",
            headers=headers,
            params=params,
            json=body,
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(f"Supabase {table}: {response.status_code} {response.text[:500]}")
        return response.json() if response.content else None

    def upsert(self, table: str, rows: Any, *, conflict: str) -> None:
        self.request(
            "POST",
            table,
            params={"on_conflict": conflict},
            body=rows,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def delete_before(self, table: str, column: str, cutoff: str) -> None:
        """보존 경계보다 오래된 시계열 행을 삭제한다."""
        self.request(
            "DELETE",
            table,
            params={column: f"lt.{cutoff}"},
            prefer="return=minimal",
        )


def refresh_kakao_access_token(*, required: bool) -> str | None:
    """카카오 refresh token으로 단기 access token을 발급한다."""
    client_id = os.getenv("KAKAO_REST_API_KEY", "").strip()
    refresh_token = os.getenv("KAKAO_REFRESH_TOKEN", "").strip()
    if not client_id or not refresh_token:
        if required:
            missing = "KAKAO_REST_API_KEY" if not client_id else "KAKAO_REFRESH_TOKEN"
            raise RuntimeError(f"Missing required environment variable: {missing}")
        return None
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    client_secret = os.getenv("KAKAO_CLIENT_SECRET", "").strip()
    if client_secret:
        data["client_secret"] = client_secret
    response = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=data,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    access_token = payload.get("access_token")
    if required and not access_token:
        raise RuntimeError("Kakao access token was not returned")
    if payload.get("refresh_token"):
        print("Kakao issued a new refresh token. Update KAKAO_REFRESH_TOKEN secret soon.")
    return str(access_token) if access_token else None


def send_kakao_text(access_token: str, message: str) -> None:
    """MacroWatch 링크가 포함된 카카오 나와의 채팅 메시지를 전송한다."""
    template = {
        "object_type": "text",
        "text": message,
        "link": {"web_url": MACROWATCH_URL, "mobile_web_url": MACROWATCH_URL},
    }
    response = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=30,
    )
    response.raise_for_status()
