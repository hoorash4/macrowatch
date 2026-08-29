from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from typing import Any
from urllib.parse import quote

import requests

from common import (
    SupabaseRest,
    fetch_fred_observations,
    require_env,
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


def kakao_message(results: list[CheckResult]) -> str:
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
    return "\n".join(lines)


def enqueue_alerts(
    db: SupabaseRest,
    results: list[CheckResult],
 ) -> list[dict[str, Any]]:
    if not results:
        return []
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
                "status": "pending",
                "error_message": None,
            }
        )
    return db.request("POST", "alert_events", body=rows, prefer="return=representation") or []


def queued_alerts(db: SupabaseRest) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return db.request(
        "GET",
        "alert_events",
        params={
            "select": "id,target_id,user_id,previous_value,current_value,condition_type,target_value,status,attempt_count,created_at",
            "status": "in.(pending,failed)",
            "attempt_count": "lt.5",
            "created_at": f"gte.{cutoff}",
            "order": "created_at.asc",
            "limit": "100",
        },
    ) or []


def result_from_event(event: dict[str, Any], target_titles: dict[Any, str]) -> CheckResult:
    target_id = event.get("target_id")
    target = {
        "id": target_id,
        "user_id": event.get("user_id"),
        "title": target_titles.get(target_id) or f"지표 {target_id}",
        "condition_type": event.get("condition_type") or "changed",
        "target_value": event.get("target_value"),
    }
    previous = parse_decimal(event["previous_value"]) if event.get("previous_value") not in (None, "") else None
    return CheckResult(target, previous, parse_decimal(event["current_value"]), True)


def alert_chunks(events: list[dict[str, Any]], target_titles: dict[Any, str], max_chars: int = 1600) -> list[tuple[list[dict[str, Any]], str]]:
    chunks: list[tuple[list[dict[str, Any]], str]] = []
    current: list[dict[str, Any]] = []
    for event in events:
        candidate = [*current, event]
        message = kakao_message([result_from_event(item, target_titles) for item in candidate])
        if current and len(message) > max_chars:
            chunks.append((current, kakao_message([result_from_event(item, target_titles) for item in current])))
            current = [event]
        else:
            current = candidate
    if current:
        chunks.append((current, kakao_message([result_from_event(item, target_titles) for item in current])))
    return chunks


def update_delivery_events(
    db: SupabaseRest,
    events: list[dict[str, Any]],
    *,
    status: str,
    error_message: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for event in events:
        body: dict[str, Any] = {
            "status": status,
            "error_message": error_message,
            "last_attempt_at": now,
            "attempt_count": int(event.get("attempt_count") or 0) + 1,
        }
        if status == "sent":
            body["sent_at"] = now
        db.request(
            "PATCH",
            "alert_events",
            params={"id": f"eq.{event['id']}"},
            body=body,
            prefer="return=minimal",
        )


def deliver_queued_alerts(db: SupabaseRest, targets: list[dict[str, Any]]) -> tuple[int, int]:
    events = queued_alerts(db)
    if not events:
        return 0, 0

    target_titles = {target["id"]: str(target.get("title") or f"지표 {target['id']}") for target in targets}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    undeliverable: list[dict[str, Any]] = []
    for event in events:
        user_id = str(event.get("user_id") or "").strip()
        if user_id:
            grouped[user_id].append(event)
        else:
            undeliverable.append(event)

    failures = 0
    delivered = 0
    if undeliverable:
        update_delivery_events(
            db,
            undeliverable,
            status="skipped",
            error_message="알림 대상 사용자 정보가 없습니다.",
        )
        failures += 1

    for user_id, user_events in grouped.items():
        for chunk, message in alert_chunks(user_events, target_titles):
            try:
                response = db.invoke_function(
                    "kakao-auth",
                    {"action": "send_internal", "user_id": user_id, "text": message},
                )
                if not isinstance(response, dict) or response.get("sent") is not True:
                    raise RuntimeError("카카오 전송 함수가 성공을 확인하지 않았습니다.")
                update_delivery_events(db, chunk, status="sent", error_message=None)
                delivered += len(chunk)
                print(f"Kakao notification sent for {len(chunk)} target(s).")
            except Exception as exc:
                failures += 1
                message_text = str(exc)[:1000]
                update_delivery_events(db, chunk, status="failed", error_message=message_text)
                print(f"Kakao notification failed: {message_text}", file=sys.stderr)
    return delivered, failures


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

    enqueue_alerts(db, alerts)
    delivered, notification_failures = deliver_queued_alerts(db, targets)

    print(
        f"Finished: {len(targets)} target(s), {len(alerts)} new alert(s), "
        f"{delivered} delivered alert(s), {failures} source failure(s), "
        f"{notification_failures} notification failure(s)."
    )
    # A source failure is recorded on the target and must not block the other targets
    # or mark the twice-daily collection job itself as broken.
    return 1 if notification_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

