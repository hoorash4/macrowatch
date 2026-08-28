"""Attach official filing identity to historical OpenDART backfill jobs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from hashlib import sha256
import json
import os
from threading import local
import time
from zoneinfo import ZoneInfo

from earnings.filings import parse_periodic_filings
from earnings.open_dart import OpenDartClient
from earnings.supabase_rest import SupabaseEarningsStore


OPERATION = "backfill_periodic_filings"
_worker_state = local()


def canonical_payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def filing_window(years: list[int], today: date) -> tuple[date, date] | None:
    if not years:
        return None
    begin = date(min(years), 1, 1)
    end = min(date(max(years), 12, 31), today)
    return (begin, end) if begin <= end else None


def fetch_company_year(
    corp_code: str,
    year: int,
    today: date,
    interval: float,
) -> tuple[str, int, date, date, list]:
    """Fetch one accepted one-year range using a thread-local HTTP session."""
    window = filing_window([year], today)
    if window is None:
        return corp_code, year, today, today, []
    begin, end = window
    client = getattr(_worker_state, "client", None)
    if client is None:
        client = OpenDartClient.from_env()
        _worker_state.client = client
    responses = list(client.iter_periodic_filings(begin, end, corp_code=corp_code))
    if interval:
        time.sleep(interval)
    return corp_code, year, begin, end, responses


def main() -> None:
    store = SupabaseEarningsStore.from_env()
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    interval = max(0.3, float(os.getenv("OPEN_DART_REQUEST_INTERVAL_SECONDS", "0.3")))
    workers = max(1, min(int(os.getenv("OPEN_DART_FILING_WORKERS", "4")), 4))
    gaps = store.list_open_dart_identity_gaps()
    pages = 0
    filings = 0
    updated = 0
    unmatched = 0

    requests = []
    for gap in gaps:
        corp_code = str(gap.get("corp_code") or "").strip()
        if len(corp_code) == 8 and corp_code.isdigit():
            requests.extend(
                (corp_code, int(year)) for year in sorted(set(gap.get("years") or []))
            )

    # OpenDART rejects the full five-year range. Four one-year workers plus a
    # per-worker delay stay below the provider's ten-requests-per-second limit
    # while avoiding the original fully sequential backfill.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(fetch_company_year, corp_code, year, today, interval)
            for corp_code, year in requests
        ]
        for future in as_completed(futures):
            corp_code, year, begin, end, responses = future.result()
            for page_number, response in enumerate(responses, start=1):
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
                result = store.attach_open_dart_backfill_filings([
                    filing.as_payload() for filing in parsed
                ])
                filings += len(parsed)
                updated += int(result.get("updated") or 0)
                unmatched += int(result.get("unmatched") or 0)

    print(json.dumps({
        "ok": True,
        "companies": len(gaps),
        "requests": len(requests),
        "pages": pages,
        "filings": filings,
        "updated": updated,
        "unmatched": unmatched,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
