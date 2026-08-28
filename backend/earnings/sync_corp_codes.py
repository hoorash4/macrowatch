"""Connect current Korean earnings companies to OpenDART corporation codes."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from earnings.corp_codes import DartCorporation, listed_corporations, parse_corp_code_archive
from earnings.open_dart import OpenDartClient
from earnings.supabase_rest import SupabaseEarningsStore


def build_identifier_rows(
    companies: list[dict[str, Any]],
    listed_by_ticker: Mapping[str, DartCorporation],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build only exact stock-code matches; unresolved tickers remain retryable."""
    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for company in companies:
        ticker = str(company.get("ticker") or "").strip()
        corporation = listed_by_ticker.get(ticker)
        if not corporation:
            if ticker:
                unresolved.append(ticker)
            continue
        rows.append({
            "ticker": ticker,
            "corp_code": corporation.corp_code,
        })
    return rows, sorted(unresolved)


def main() -> None:
    client = OpenDartClient.from_env()
    store = SupabaseEarningsStore.from_env()
    archive = client.fetch_corp_code_archive()
    listed = listed_corporations(parse_corp_code_archive(archive.content))
    companies = store.list_active_korean_companies()
    korean_today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    rows, unresolved = build_identifier_rows(companies, listed)
    sync_result = store.sync_open_dart_identifiers(rows, valid_from=korean_today.isoformat())
    backfill_queued = store.enqueue_open_dart_backfill(as_of_year=korean_today.year, years=5)

    # Only aggregate counts and stock codes are emitted. Provider URLs, request
    # parameters, headers, and secrets never enter the workflow log.
    print(json.dumps({
        "ok": True,
        "tracked_companies": len(companies),
        "dart_identifiers_matched": int(sync_result.get("matched") or 0),
        "unresolved_count": len(unresolved),
        "unresolved_tickers": unresolved,
        "backfill_jobs_queued": backfill_queued,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
