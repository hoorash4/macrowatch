"""Connect current Korean earnings companies to OpenDART corporation codes."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from earnings.corp_codes import DartCorporation, listed_corporations, parse_corp_code_archive
from earnings.collection_coverage import validate_collection_universe
from earnings.open_dart import OpenDartClient
from earnings.supabase_rest import SupabaseEarningsStore


# OpenDART's structured financial-statement endpoint accepts business years
# from 2015 onward. Keep 2015 on this deterministic route: it is the comparison
# base for 2016 YoY metrics, so sending it through the legacy archive parser can
# distort every downstream growth and delta series.
OPEN_DART_STRUCTURED_START_YEAR = 2015


def structured_history_years(as_of_year: int) -> int:
    """Return the non-rolling OpenDART structured-history window."""
    if as_of_year < OPEN_DART_STRUCTURED_START_YEAR:
        raise ValueError("MacroWatch structured OpenDART history starts in 2015.")
    return as_of_year - OPEN_DART_STRUCTURED_START_YEAR + 1


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
    coverage = store.get_current_collection_coverage(country="KR")
    unique_companies = validate_collection_universe(
        coverage, companies, company_id_key="id"
    )
    history_years = structured_history_years(korean_today.year)
    backfill_queued = store.enqueue_open_dart_backfill(
        as_of_year=korean_today.year,
        years=history_years,
    )

    # Only aggregate counts and stock codes are emitted. Provider URLs, request
    # parameters, headers, and secrets never enter the workflow log.
    print(json.dumps({
        "ok": True,
        "tracked_companies": len(companies),
        "unique_companies": unique_companies,
        "dart_identifiers_matched": int(sync_result.get("matched") or 0),
        "unresolved_count": len(unresolved),
        "unresolved_tickers": unresolved,
        "backfill_jobs_queued": backfill_queued,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
