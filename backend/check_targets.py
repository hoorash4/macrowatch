from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


HTTP_TIMEOUT = 15
KST = timezone(timedelta(hours=9))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class CollectionError(RuntimeError):
    pass


@dataclass
class CheckResult:
    target: dict[str, Any]
    previous_value: Decimal | None
    current_value: Decimal
    should_alert: bool


class SupabaseRest:
    def __init__(self) -> None:
        self.url = require_env("SUPABASE_URL").rstrip("/")
        self.key = require_env("SUPABASE_SERVICE_ROLE_KEY")
        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

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
        response = requests.request(
            method,
            f"{self.url}/rest/v1/{table}",
            headers=headers,
            params=params,
            json=body,
            timeout=HTTP_TIMEOUT,
        )
        if not response.ok:
            raise RuntimeError(f"Supabase {table}: {response.status_code} {response.text[:500]}")
        if not response.content:
            return None
        return response.json()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Cache-Control": "no-cache",
    }
    headers.update(extra or {})
    return headers


def parse_decimal(value: Any) -> Decimal:
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))

    text = str(value or "").strip().replace("−", "-").replace("–", "-")
    if not text or text == ".":
        raise CollectionError("수치가 비어 있습니다.")

    negative_parentheses = text.startswith("(") and text.endswith(")")
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        raise CollectionError(f"수치를 찾을 수 없습니다: {text[:120]}")

    normalized = match.group(0).replace(",", "")
    if negative_parentheses and not normalized.startswith("-"):
        normalized = f"-{normalized}"
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise CollectionError(f"수치 변환에 실패했습니다: {text[:120]}") from exc


def nested_value(payload: Any, path: str) -> Any:
    current = payload
    for part in filter(None, path.split(".")):
        list_match = re.fullmatch(r"([^\[]*)\[(\d+)]", part)
        if list_match:
            key, index_text = list_match.groups()
            if key:
                current = current[key]
            current = current[int(index_text)]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            current = current[part]
    return current


def fetch_fred(config: dict[str, Any]) -> Decimal:
    series_id = str(config.get("series_id", "")).strip().upper()
    if not series_id:
        raise CollectionError("FRED series_id가 없습니다.")
    response = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": require_env("FRED_API_KEY"),
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,
        },
        headers=request_headers({"Accept": "application/json"}),
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    for observation in response.json().get("observations", []):
        if observation.get("value") not in (None, "."):
            return parse_decimal(observation["value"])
    raise CollectionError(f"FRED {series_id}의 최신값이 없습니다.")


def ecos_date_range(cycle: str) -> tuple[str, str]:
    now = datetime.now(KST)
    if cycle == "D":
        return (now - timedelta(days=45)).strftime("%Y%m%d"), now.strftime("%Y%m%d")
    if cycle == "M":
        return (now - timedelta(days=730)).strftime("%Y%m"), now.strftime("%Y%m")
    if cycle == "Q":
        start_year = now.year - 5
        return f"{start_year}Q1", f"{now.year}Q4"
    if cycle == "S":
        start_year = now.year - 8
        return f"{start_year}S1", f"{now.year}S2"
    if cycle == "A":
        return str(now.year - 15), str(now.year)
    raise CollectionError(f"지원하지 않는 ECOS 주기입니다: {cycle}")


def fetch_ecos(config: dict[str, Any]) -> Decimal:
    stat_code = str(config.get("stat_code", "")).strip().upper()
    item_code = str(config.get("item_code", "")).strip()
    cycle = str(config.get("data_cycle", "D")).strip().upper()
    if not stat_code or not item_code:
        raise CollectionError("ECOS 통계표 코드 또는 항목 코드가 없습니다.")

    start_time, end_time = ecos_date_range(cycle)
    parts = [
        "https://ecos.bok.or.kr/api/StatisticSearch",
        quote(require_env("ECOS_API_KEY"), safe=""),
        "json",
        "kr",
        "1",
        "100",
        quote(stat_code, safe=""),
        quote(cycle, safe=""),
        quote(start_time, safe=""),
        quote(end_time, safe=""),
        quote(item_code, safe=""),
    ]
    response = requests.get("/".join(parts), headers=request_headers(), timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("StatisticSearch", {}).get("row", [])
    for row in reversed(rows):
        if row.get("DATA_VALUE") not in (None, ""):
            return parse_decimal(row["DATA_VALUE"])
    error = payload.get("RESULT", payload.get("StatisticSearch", {}).get("RESULT", {}))
    raise CollectionError(f"ECOS 최신값이 없습니다: {error}")


def fetch_json_api(target: dict[str, Any], config: dict[str, Any]) -> Decimal:
    response = requests.get(
        target["url"],
        headers=request_headers({"Accept": "application/json"}),
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    path = str(config.get("json_path", "")).strip()
    if not path and str(target.get("css_selector", "")).startswith("API:"):
        path = str(target["css_selector"])[4:]
    if not path:
        raise CollectionError("JSON 추출 경로가 없습니다.")
    return parse_decimal(nested_value(response.json(), path))


def fetch_static_html(url: str, selector: str, config: dict[str, Any]) -> Decimal:
    response = requests.get(
        url,
        headers=request_headers(config.get("headers") if isinstance(config.get("headers"), dict) else None),
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    element = BeautifulSoup(response.text, "html.parser").select_one(selector)
    if element is None:
        raise CollectionError("일반 HTML 응답에서 CSS 선택자를 찾지 못했습니다.")
    attribute = str(config.get("attribute", "")).strip()
    return parse_decimal(element.get(attribute) if attribute else element.get_text(" ", strip=True))


class BrowserCollector:
    def __init__(self) -> None:
        self.playwright = None
        self.browser = None
        self.context = None

    def __enter__(self) -> "BrowserCollector":
        return self

    def __exit__(self, *_: object) -> None:
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def _ensure_context(self) -> None:
        if self.context:
            return
        self.playwright = sync_playwright().start()
        # GitHub's Ubuntu runner already includes Chrome, so no browser download is needed.
        self.browser = self.playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self.context = self.browser.new_context(
            user_agent=USER_AGENT,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 1000},
        )

    def fetch(self, url: str, selector: str, config: dict[str, Any]) -> Decimal:
        self._ensure_context()
        timeout_ms = min(max(int(config.get("timeout_ms", 12000)), 3000), 15000)
        page = self.context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.locator(selector).first.wait_for(state="attached", timeout=timeout_ms)
            attribute = str(config.get("attribute", "")).strip()
            locator = page.locator(selector).first
            raw_value = locator.get_attribute(attribute) if attribute else locator.inner_text(timeout=timeout_ms)
            return parse_decimal(raw_value)
        except PlaywrightTimeoutError as exc:
            title = page.title()[:100]
            raise CollectionError(f"브라우저 렌더링 후에도 값을 찾지 못했습니다: {title}") from exc
        finally:
            page.close()


def fetch_web(
    target: dict[str, Any], config: dict[str, Any], browser: BrowserCollector
) -> Decimal:
    url = str(target.get("url", "")).strip()
    selector = str(config.get("selector") or target.get("css_selector") or "").strip()
    if not url or not selector:
        raise CollectionError("웹페이지 URL 또는 CSS 선택자가 없습니다.")
    try:
        return fetch_static_html(url, selector, config)
    except Exception as static_error:
        try:
            return browser.fetch(url, selector, config)
        except Exception as rendered_error:
            raise CollectionError(
                f"일반 요청 실패: {static_error}; 브라우저 요청 실패: {rendered_error}"
            ) from rendered_error


def collect_value(target: dict[str, Any], browser: BrowserCollector) -> Decimal:
    source_type = str(target.get("source_type") or "web").lower()
    config = target.get("source_config") if isinstance(target.get("source_config"), dict) else {}
    if source_type == "fred":
        return fetch_fred(config)
    if source_type == "ecos":
        return fetch_ecos(config)
    if source_type == "json_api":
        return fetch_json_api(target, config)
    if source_type == "web":
        return fetch_web(target, config, browser)
    raise CollectionError(f"아직 지원하지 않는 source_type입니다: {source_type}")


def condition_met(target: dict[str, Any], previous: Decimal | None, current: Decimal) -> bool:
    condition = str(target.get("condition_type") or "changed")
    if previous is None:
        return False
    if condition == "changed":
        return current != previous

    raw_target_value = target.get("target_value")
    if raw_target_value is None:
        return False
    threshold = parse_decimal(raw_target_value)
    if condition == "gte":
        return previous <= threshold < current
    if condition == "lte":
        return previous >= threshold > current
    if condition == "cross":
        return (previous <= threshold < current) or (previous >= threshold > current)
    return False


def format_number(value: Decimal | None) -> str:
    if value is None:
        return "—"
    normalized = format(value.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def condition_label(condition: str) -> str:
    return {
        "gte": "설정값 상향 돌파",
        "lte": "설정값 하향 돌파",
        "cross": "설정값 상/하향 돌파",
        "changed": "지표값 변동 감지",
    }.get(condition, condition)


def refresh_kakao_access_token() -> str | None:
    client_id = os.getenv("KAKAO_REST_API_KEY", "").strip()
    refresh_token = os.getenv("KAKAO_REFRESH_TOKEN", "").strip()
    if not client_id or not refresh_token:
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
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("refresh_token"):
        print("Kakao issued a new refresh token. Update KAKAO_REFRESH_TOKEN secret soon.")
    return payload.get("access_token")


def send_kakao_message(access_token: str, results: list[CheckResult]) -> None:
    lines = ["[MacroWatch] 알림 조건을 충족했습니다."]
    for result in results:
        condition = str(result.target.get("condition_type") or "")
        lines.extend(
            [
                "",
                str(result.target.get("title") or "이름 없는 지표"),
                f"{format_number(result.previous_value)} → {format_number(result.current_value)}",
                condition_label(condition),
            ]
        )
    template = {
        "object_type": "text",
        "text": "\n".join(lines),
        "link": {
            "web_url": "https://hoorash4.github.io/macrowatch/",
            "mobile_web_url": "https://hoorash4.github.io/macrowatch/",
        },
    }
    response = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()


def record_alerts(
    db: SupabaseRest,
    results: list[CheckResult],
    status: str,
    error_message: str | None = None,
) -> None:
    if not results:
        return
    rows = []
    for result in results:
        target = result.target
        rows.append(
            {
                "target_id": target["id"],
                "user_id": target.get("user_id"),
                "previous_value": json_number(result.previous_value),
                "current_value": json_number(result.current_value),
                "condition_type": target.get("condition_type") or "changed",
                "target_value": target.get("target_value"),
                "channel": "kakao_self",
                "status": status,
                "error_message": error_message,
            }
        )
    db.request("POST", "alert_events", body=rows, prefer="return=minimal")


def main() -> int:
    db = SupabaseRest()
    targets = db.request(
        "GET",
        "targets",
        params={"select": "*", "is_active": "eq.true", "order": "display_order.asc.nullslast"},
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    alerts: list[CheckResult] = []
    failures = 0

    with BrowserCollector() as browser:
        for target in targets:
            target_id = target["id"]
            try:
                previous = parse_decimal(target["last_value"]) if target.get("last_value") not in (None, "") else None
                current = collect_value(target, browser)
                result = CheckResult(target, previous, current, condition_met(target, previous, current))

                db.request(
                    "PATCH",
                    "targets",
                    params={"id": f"eq.{target_id}"},
                    body={
                        "last_value": json_number(current),
                        "last_checked_at": now_iso,
                        "last_error": None,
                    },
                    prefer="return=minimal",
                )
                if result.should_alert:
                    alerts.append(result)
                print(f"OK {target_id}: {target.get('title')} = {format_number(current)}")
            except Exception as exc:
                failures += 1
                message = str(exc)[:1000]
                print(f"ERROR {target_id}: {target.get('title')}: {message}", file=sys.stderr)
                db.request(
                    "PATCH",
                    "targets",
                    params={"id": f"eq.{target_id}"},
                    body={"last_checked_at": now_iso, "last_error": message},
                    prefer="return=minimal",
                )

    if alerts:
        access_token = refresh_kakao_access_token()
        if not access_token:
            record_alerts(db, alerts, "skipped", "Kakao secrets are not configured.")
            print("Kakao notification skipped: secrets are not configured.")
        else:
            try:
                send_kakao_message(access_token, alerts)
                record_alerts(db, alerts, "sent")
                print(f"Kakao notification sent for {len(alerts)} target(s).")
            except Exception as exc:
                message = str(exc)[:1000]
                record_alerts(db, alerts, "failed", message)
                print(f"Kakao notification failed: {message}", file=sys.stderr)
                failures += 1

    print(f"Finished: {len(targets)} target(s), {len(alerts)} alert(s), {failures} failure(s).")
    # A source failure is recorded on the target and must not block the other targets
    # or mark the twice-daily collection job itself as broken.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
