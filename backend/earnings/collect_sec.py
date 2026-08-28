"""Refresh ten years of SEC company facts for both independent U.S. universes."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import requests

from earnings.sec_edgar import SecCompanyFactsMirrorClient, SecEdgarClient
from earnings.sec_parser import canonical_sec_quarters
from earnings.supabase_rest import SupabaseEarningsStore


def main() -> None:
    store = SupabaseEarningsStore.from_env()
    client = SecEdgarClient.from_env()
    today = datetime.now(ZoneInfo("America/New_York")).date()
    companies = store.list_current_sec_companies()
    totals = Counter(companies=len(companies))
    errors: Counter[str] = Counter()
    mirror_client: SecCompanyFactsMirrorClient | None = None
    sec_blocked = False

    for company in companies:
        company_id = str(company.get("company_id") or "").strip()
        cik = str(company.get("cik") or "").strip()
        ticker = str(company.get("ticker") or "").strip()
        try:
            if sec_blocked:
                if mirror_client is None:
                    mirror_client = SecCompanyFactsMirrorClient()
                payload = mirror_client.fetch_company_facts(cik, first_year=today.year - 9)
                totals["mirror_companies"] += 1
            else:
                try:
                    payload = client.fetch_company_facts(cik)
                except requests.HTTPError as error:
                    if error.response is None or error.response.status_code != 403:
                        raise
                    # SEC commonly blocks GitHub's shared runner addresses even
                    # with a compliant identity. Switch once, then keep all
                    # remaining companies on the nightly SEC-derived mirror.
                    sec_blocked = True
                    mirror_client = SecCompanyFactsMirrorClient()
                    payload = mirror_client.fetch_company_facts(cik, first_year=today.year - 9)
                    totals["mirror_companies"] += 1
            rows, gaps = canonical_sec_quarters(
                payload,
                cik=cik,
                as_of_year=today.year,
                years=10,
            )
            result = store.upsert_sec_company_quarters(company_id=company_id, rows=rows)
            totals["quarters_seen"] += int(result.get("seen") or 0)
            totals["quarters_changed"] += int(result.get("changed") or 0)
            totals["gaps"] += len(gaps)
            totals["completed_companies"] += 1
        except Exception as error:
            totals["failed_companies"] += 1
            safe = " ".join(str(error).split())[:240]
            errors[f"{ticker or cik}: {type(error).__name__}: {safe}"] += 1

    store.save_checkpoint(
        source="sec_edgar",
        operation="company_facts_refresh",
        cursor={
            "through_date": today.isoformat(),
            "companies": totals["completed_companies"],
            "failed": totals["failed_companies"],
        },
    )
    summary = {"ok": totals["failed_companies"] == 0, **totals, "errors": dict(errors.most_common(20))}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if totals["failed_companies"]:
        raise RuntimeError(f"SEC refresh failed for {totals['failed_companies']} companies.")


if __name__ == "__main__":
    main()
