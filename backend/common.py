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

# These FRED identifiers are aliases for first-party U.S. Treasury data.  Keep
# accepting the identifiers at shared call sites for compatibility, but route
# them to Treasury so active collectors are not held back by FRED publication lag.
TREASURY_FRED_ALIASES = frozenset({"DGS3MO", "DGS2", "DGS10", "DFII10", "T10Y2Y"})

FRED_HTTP_SESSION: requests.Session | None = None
TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
HTTP_RETRY_COUNT = 3


def request_with_retry(
    send: Any,
    *,
    retry_count: int = HTTP_RETRY_COUNT,
    retry_transport: bool = True,
    sleep: Any = time.sleep,
) -> requests.Response:
    """Run one idempotent HTTP request with one initial try and bounded retries.

    A write whose outcome cannot be known safely must not be passed here.  The
    caller chooses this helper only for reads, idempotent upserts/deletes, or
    an endpoint whose server-side operation is explicitly idempotent.
    """
    last_error: requests.RequestException | None = None
    response: requests.Response | None = None
    for attempt in range(retry_count + 1):
        try:
            response = send()
        except requests.RequestException as exc:
            last_error = exc
            if not retry_transport or attempt == retry_count:
                raise
            delay = float(2 ** attempt)
        else:
            if getattr(response, "status_code", None) not in TRANSIENT_HTTP_STATUSES or attempt == retry_count:
                return response
            retry_after = getattr(response, "headers", {}).get("Retry-After")
            try:
                delay = max(float(retry_after), 0.0) if retry_after else float(2 ** attempt)
            except (TypeError, ValueError):
                delay = float(2 ** attempt)
        sleep(min(delay, 30.0))
    if response is not None:
        return response
    raise RuntimeError("HTTP request did not produce a response") from last_error


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


def _treasury_alias_observations(
    series_id: str,
    *,
    start: str | None,
    end: str | None,
    sort_order: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Return Treasury first-party data in FRED observation-compatible shape."""
    from sources.us_treasury_yields import fetch_treasury_nominal_values, fetch_treasury_real_values

    start_date = date.fromisoformat(start) if start else date(1990, 1, 1)
    end_date = date.fromisoformat(end) if end else date.today()
    if end_date < start_date:
        return []

    if series_id == "DFII10":
        values = fetch_treasury_real_values(start_date, end_date, ("10Y",))["10Y"]
    else:
        maturities = {
            "DGS3MO": ("3M",),
            "DGS2": ("2Y",),
            "DGS10": ("10Y",),
            "T10Y2Y": ("2Y", "10Y"),
        }[series_id]
        nominal = fetch_treasury_nominal_values(start_date, end_date, maturities)
        if series_id == "T10Y2Y":
            common_dates = nominal["10Y"].keys() & nominal["2Y"].keys()
            values = {observed: nominal["10Y"][observed] - nominal["2Y"][observed] for observed in common_dates}
        else:
            values = nominal[maturities[0]]

    rows = [{"date": observed.isoformat(), "value": str(round(value, 8))} for observed, value in sorted(values.items())]
    if str(sort_order or "").lower() == "desc":
        rows.reverse()
    return rows[:max(int(limit), 0)]


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
    """Fetch observations, preferring first-party Treasury for Treasury series.

    The legacy function name remains to avoid needless churn in callers.  DGS/DFII/T10Y2Y
    aliases are sourced directly from Treasury; genuinely FRED-only series still use FRED.
    """
    if series_id in TREASURY_FRED_ALIASES and session is None:
        return _treasury_alias_observations(
            series_id,
            start=start,
            end=end,
            sort_order=sort_order,
            limit=limit,
        )

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
    client = session or default_fred_session()
    response = request_with_retry(
        lambda: client.get(FRED_OBSERVATIONS_URL, params=params, headers=headers, timeout=timeout),
    )
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
        retry_safe: bool | None = None,
    ) -> Any:
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        send = lambda: self.session.request(
            method, f"{self.url}/rest/v1/{table}", headers=headers,
            params=params, json=body, timeout=self.timeout,
        )
        is_transport_safe = retry_safe if retry_safe is not None else method.upper() in {"GET", "DELETE"}
        response = request_with_retry(send, retry_transport=is_transport_safe)
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
            retry_safe=True,
        )

    def automatic_rows(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        key: str,
        provisional: str | None = None,
        refresh_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """자동수집에서는 신규·잠정·명시한 현재 주기 행만 저장한다."""
        if not rows:
            return []
        select = key + (f",{provisional}" if provisional else "")
        existing = self.request(
            "GET",
            table,
            params={
                "select": select,
                key: f"gte.{min(str(row[key]) for row in rows)}",
                "limit": "10000",
            },
        ) or []
        state = {str(row[key]): bool(row.get(provisional)) if provisional else False for row in existing}
        forced = refresh_keys or set()
        return [
            row for row in rows
            if str(row[key]) not in state or state[str(row[key])] or str(row[key]) in forced
        ]

    def invoke_function(self, name: str, body: Any) -> Any:
        """서비스 역할 자격으로 내부 Supabase Edge Function을 호출한다."""
        response = self.session.post(
            f"{self.url}/functions/v1/{name}",
            headers=self.headers,
            json=body,
            timeout=self.timeout,
        )
        if not response.ok:
            detail = response.text[:500]
            try:
                payload = response.json()
                detail = str(payload.get("error") or payload.get("message") or detail)
            except (TypeError, ValueError):
                pass
            raise RuntimeError(f"Supabase function {name}: {response.status_code} {detail}")
        return response.json() if response.content else None

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
