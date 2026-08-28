from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import requests

from common import (
    SupabaseRest,
    fetch_fred_observations,
    refresh_kakao_access_token as refresh_shared_kakao_token,
    require_env,
    send_kakao_text,
)


HTTP_TIMEOUT = 30
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


def fetch_fred(config: dict[str, Any]) -> Decimal:
    series_id = str(config.get("series_id", "")).strip().upper()
    if not series_id:
        raise CollectionError("FRED series_id가 없습니다.")
    observations = fetch_fred_observations(
        series_id,
        require_env("FRED_API_KEY"),
        sort_order="desc",
        limit=10,
        headers=request_headers({"Accept": "application/json"}),
        timeout=HTTP_TIMEOUT,
    )
    for observation in observations:
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
    if not stat_code:
        raise CollectionError("ECOS 통계표 코드가 없습니다.")

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
    ]
    if item_code:
        parts.append(quote(item_code, safe=""))
    response = requests.get("/".join(parts), headers=request_headers(), timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("StatisticSearch", {}).get("row", [])
    for row in reversed(rows):
        if row.get("DATA_VALUE") not in (None, ""):
            return parse_decimal(row["DATA_VALUE"])
    error = payload.get("RESULT", payload.get("StatisticSearch", {}).get("RESULT", {}))
    raise CollectionError(f"ECOS 최신값이 없습니다: {error}")


def collect_value(target: dict[str, Any]) -> Decimal:
    source_type = str(target.get("source_type") or "").lower()
    config = target.get("source_config") if isinstance(target.get("source_config"), dict) else {}
    if source_type == "fred":
        return fetch_fred(config)
    if source_type == "ecos":
        return fetch_ecos(config)
    raise CollectionError(f"지원하지 않는 데이터 소스입니다: {source_type}")


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
    return refresh_shared_kakao_token(required=False)


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
    send_kakao_text(access_token, "\n".join(lines))


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

    for target in targets:
        target_id = target["id"]
        try:
            previous = (
                parse_decimal(target["last_value"])
                if target.get("last_value") not in (None, "")
                else None
            )
            current = collect_value(target)
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
        # 지표 수집과 카카오 전송은 서로 다른 책임이다. 토큰 만료나 카카오
        # 장애가 발생해도 이미 끝난 지표 수집 실행까지 실패로 바꾸지 않는다.
        try:
            access_token = refresh_kakao_access_token()
            if not access_token:
                record_alerts(db, alerts, "skipped", "Kakao secrets are not configured.")
                print("Kakao notification skipped: secrets are not configured.")
            else:
                send_kakao_message(access_token, alerts)
                record_alerts(db, alerts, "sent")
                print(f"Kakao notification sent for {len(alerts)} target(s).")
        except Exception as exc:
            message = str(exc)[:1000]
            try:
                record_alerts(db, alerts, "failed", message)
            except Exception as record_error:
                print(f"Kakao alert failure record failed: {str(record_error)[:1000]}", file=sys.stderr)
            print(f"Kakao notification failed: {message}", file=sys.stderr)

    print(f"Finished: {len(targets)} target(s), {len(alerts)} alert(s), {failures} failure(s).")
    # A source failure is recorded on the target and must not block the other targets
    # or mark the twice-daily collection job itself as broken.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

