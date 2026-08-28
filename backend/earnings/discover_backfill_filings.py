"""Attach official filing identity to historical OpenDART backfill jobs."""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
import json
import os
import time
from zoneinfo import ZoneInfo

from earnings.filings import parse_periodic_filings
from earnings.open_dart import OpenDartClient
from earnings.supabase_rest import SupabaseEarningsStore


OPERATION = "backfill_periodic_filings"


def canonical_payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def filing_window(year: int, today: date) -> tuple[date, date] | None:
    begin = date(year, 1, 1)
    end = min(date(year, 12, 31), today)
    return (begin, end) if begin <= end else None


def main() -> None:
    client = OpenDartClient.from_env()
    store = SupabaseEarningsStore.from_env()
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    interval = max(0.0, float(os.getenv("OPEN_DART_REQUEST_INTERVAL_SECONDS", "0.2")))
    gaps = store.list_open_dart_identity_gaps()
    pages = 0
    filings = 0
    queued = 0

    for gap in gaps:
        corp_code = str(gap.get("corp_code") or "").strip()
        years = sorted({int(year) for year in gap.get("years") or []})
        if len(corp_code) != 8 or not corp_code.isdigit():
            continue
        for year in years:
            window = filing_window(year, today)
            if window is None:
                continue
            begin, end = window
            for page_number, response in enumerate(
                client.iter_periodic_filings(begin, end, corp_code=corp_code),
                start=1,
            ):
                pages += 1
                store.save_source_payload(
                    operation=OPERATION,
                    request_key=(
                        f"{corp_code}:{begin.isoformat()}:{end.isoformat()}:page:{page_number}"
                    ),
                    request_params=response.request_params,
                    payload_sha256=canonical_payload_hash(response.payload),
                    payload=response.payload,
                )
                parsed = [
                    filing for filing in parse_periodic_filings(response.rows)
                    if filing.corp_code == corp_code and filing.business_year == year
                ]
                result = store.enqueue_open_dart_filings([
                    filing.as_payload() for filing in parsed
                ])
                filings += len(parsed)
                queued += int(result.get("queued") or 0)
                if interval:
                    time.sleep(interval)

    print(json.dumps({
        "ok": True,
        "companies": len(gaps),
        "pages": pages,
        "filings": filings,
        "queued": queued,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

