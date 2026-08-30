"""Backfill 2002-2015 Korean quarters from official DART filing archives."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
import os
import time
from typing import Any, Callable

from earnings.collect_financials import FILING_KIND, REPORT_END_MONTH, reporting_period_bounds
from earnings.legacy_dart_financials import (
    LegacyCumulativeStatement,
    parse_legacy_filing_archive,
)
from earnings.open_dart import OpenDartClient
from earnings.open_dart_parser import REPORT_QUARTERS, REQUIRED_METRICS
from earnings.supabase_rest import SupabaseEarningsStore


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _statement_values(statement: LegacyCumulativeStatement) -> dict[str, Decimal]:
    return {metric: getattr(statement, metric) for metric in REQUIRED_METRICS}


def build_legacy_standalone_quarters(
    statements: dict[str, dict[str, LegacyCumulativeStatement]],
) -> tuple[str | None, dict[str, dict[str, Decimal]]]:
    """Choose one year-wide scope and subtract cumulative periods safely."""
    if not statements:
        return None, {}
    common_scopes = set.intersection(*(
        set(by_scope) for by_scope in statements.values() if by_scope
    ))
    scope = next((candidate for candidate in ("CFS", "OFS") if candidate in common_scopes), None)
    if scope is None:
        return None, {}

    standalone: dict[str, dict[str, Decimal]] = {}
    previous: dict[str, Decimal] | None = None
    for report_code in ("11013", "11012", "11014", "11011"):
        by_scope = statements.get(report_code)
        if not by_scope or scope not in by_scope:
            previous = None
            continue
        cumulative = _statement_values(by_scope[scope])
        if report_code == "11013":
            values = cumulative
        elif previous is not None:
            values = {metric: cumulative[metric] - previous[metric] for metric in REQUIRED_METRICS}
        else:
            # Never invent a standalone quarter when the preceding cumulative
            # report is absent or could not be parsed.
            previous = cumulative
            continue
        standalone[report_code] = values
        previous = cumulative
    return scope, standalone


class LegacyDartFinancialWorker:
    def __init__(
        self,
        client: OpenDartClient,
        store: SupabaseEarningsStore,
        *,
        request_interval_seconds: float = 0.3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.store = store
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.sleeper = sleeper
        self._requested = False

    def _archive(self, receipt: str) -> bytes:
        if self._requested and self.request_interval_seconds:
            self.sleeper(self.request_interval_seconds)
        response = self.client.fetch_filing_archive(receipt)
        self._requested = True
        return response.content

    def process_company_year(self, jobs: list[dict[str, Any]]) -> dict[str, int]:
        result = {"completed": 0, "review_required": 0, "failed": 0}
        if not jobs:
            return result
        year = int(jobs[0]["business_year"])
        if any(int(job["business_year"]) != year for job in jobs):
            raise ValueError("Legacy DART batch must contain one company-year.")

        statements: dict[str, dict[str, LegacyCumulativeStatement]] = {}
        parse_errors: dict[str, str] = {}
        for job in jobs:
            report_code = str(job["report_code"])
            metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
            receipt = str(metadata.get("receipt_no") or "").strip()
            try:
                statements[report_code] = parse_legacy_filing_archive(
                    self._archive(receipt), report_code=report_code,
                )
            except Exception as error:
                parse_errors[report_code] = type(error).__name__

        scope, standalone = build_legacy_standalone_quarters(statements)
        for job in jobs:
            report_code = str(job["report_code"])
            metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
            receipt = str(metadata.get("receipt_no") or "").strip()
            filed_on = str(metadata.get("filed_on") or "").strip()
            try:
                period_start, period_end = reporting_period_bounds(
                    year, report_code, metadata.get("report_name"),
                )
                values = standalone.get(report_code)
                quarter = None
                outcome = "review_required"
                if scope and values:
                    quarter = {
                        "fiscal_quarter": REPORT_QUARTERS[report_code],
                        "market_year": year,
                        "market_quarter": REPORT_QUARTERS[report_code],
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                        **{metric: _decimal_text(values[metric]) for metric in REQUIRED_METRICS},
                        "currency": "KRW",
                        "consolidation_scope": scope,
                        "missing_metrics": [],
                        "source_updated_at": f"{filed_on}T00:00:00+09:00",
                    }
                    outcome = "complete"
                filing = {
                    "source_filing_id": receipt,
                    "filing_kind": FILING_KIND[report_code],
                    "fiscal_quarter": REPORT_QUARTERS[report_code],
                    "market_year": year,
                    "market_quarter": REPORT_QUARTERS[report_code],
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "filing_date": filed_on,
                    "is_correction": bool(metadata.get("is_correction")),
                    "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                    "metadata": {
                        "report_name": metadata.get("report_name"),
                        "financial_method": "legacy_dart_document_archive_v1",
                        "parse_error": parse_errors.get(report_code),
                    },
                }
                self.store.complete_open_dart_job(
                    job_id=int(job["id"]), filing=filing, quarter=quarter, outcome=outcome,
                )
                result[outcome] += 1
            except Exception as error:
                self.store.fail_open_dart_job(job_id=int(job["id"]), error=type(error).__name__)
                result["failed"] += 1
        return result


def main() -> None:
    client = OpenDartClient.from_env()
    store = SupabaseEarningsStore.from_env()
    max_company_years = max(1, min(int(os.getenv("LEGACY_DART_MAX_COMPANY_YEARS", "100")), 500))
    interval = max(0.2, float(os.getenv("OPEN_DART_REQUEST_INTERVAL_SECONDS", "0.3")))
    worker = LegacyDartFinancialWorker(
        client, store, request_interval_seconds=interval,
    )
    totals = {"company_years": 0, "completed": 0, "review_required": 0, "failed": 0}
    for _ in range(max_company_years):
        jobs = store.claim_open_dart_legacy_jobs()
        if not jobs:
            break
        batch = worker.process_company_year(jobs)
        totals["company_years"] += 1
        for key, value in batch.items():
            totals[key] += value
    print(json.dumps({"ok": totals["failed"] == 0, **totals}, ensure_ascii=False))
    if totals["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
