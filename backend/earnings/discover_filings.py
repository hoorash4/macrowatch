"""Discover new OpenDART periodic filings and enqueue resumable work."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from earnings.filings import parse_periodic_filings
from earnings.open_dart import OpenDartClient
from earnings.supabase_rest import SupabaseEarningsStore


CHECKPOINT_OPERATION = "periodic_filings"


def discovery_window(today: date, checkpoint: dict | None) -> tuple[date, date]:
    """Overlap three weekdays while preserving every missed calendar date."""
    cursor = checkpoint.get("cursor") if isinstance(checkpoint, dict) else None
    through = cursor.get("through_date") if isinstance(cursor, dict) else None
    try:
        anchor = date.fromisoformat(str(through))
    except (TypeError, ValueError):
        return today - timedelta(days=14), today

    begin = anchor
    weekdays = 0
    while weekdays < 3:
        begin -= timedelta(days=1)
        if begin.weekday() < 5:
            weekdays += 1
    return min(begin, today), today


def canonical_payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def main() -> None:
    client = OpenDartClient.from_env()
    store = SupabaseEarningsStore.from_env()
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    checkpoint = store.get_checkpoint(source="open_dart", operation=CHECKPOINT_OPERATION)
    begin, end = discovery_window(today, checkpoint)
    tracked_codes = store.list_tracked_open_dart_codes()
    discovered = []
    page_count = 0

    for response in client.iter_periodic_filings(begin, end):
        page_count += 1
        store.save_source_payload(
            operation=CHECKPOINT_OPERATION,
            request_key=f"{begin.isoformat()}:{end.isoformat()}:page:{page_count}",
            request_params=response.request_params,
            payload_sha256=canonical_payload_hash(response.payload),
            payload=response.payload,
        )
        discovered.extend(
            filing for filing in parse_periodic_filings(response.rows)
            if filing.corp_code in tracked_codes
        )

    unique = {filing.receipt_no: filing for filing in discovered}
    queue_result = store.enqueue_open_dart_filings([
        filing.as_payload() for filing in unique.values()
    ])
    store.save_checkpoint(
        source="open_dart",
        operation=CHECKPOINT_OPERATION,
        cursor={"through_date": end.isoformat()},
    )
    print(json.dumps({
        "ok": True,
        "begin_date": begin.isoformat(),
        "through_date": end.isoformat(),
        "pages": page_count,
        "tracked_filings": len(unique),
        "queued": int(queue_result.get("queued") or 0),
    }, ensure_ascii=False))
