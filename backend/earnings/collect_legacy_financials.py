"""Backfill 2002-2015 Korean quarters from official DART filing archives."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal
import json
import os
from threading import Lock, local
import time
import traceback
from typing import Any, Callable

from earnings.collect_financials import FILING_KIND, REPORT_END_MONTH, reporting_period_bounds
from earnings.financial_quality import validate_canonical_quarter
from earnings.legacy_dart_financials import (
    LegacyCumulativeStatement,
    parse_legacy_filing_archive,
)
from earnings.open_dart import OpenDartClient
from earnings.open_dart_parser import REPORT_QUARTERS, REQUIRED_METRICS
from earnings.supabase_rest import EarningsStoreError, SupabaseEarningsStore


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _statement_values(statement: LegacyCumulativeStatement) -> dict[str, Decimal]:
    return {metric: getattr(statement, metric) for metric in REQUIRED_METRICS}


def _outcome_counter_key(outcome: str) -> str:
    """Map the database outcome name to the worker summary counter."""
    return "completed" if outcome == "complete" else outcome


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
        # Revenue is the structural guard for the cumulative subtraction. A
        # non-positive standalone revenue means the parser selected a repeated
        # or prior-period column. Do not publish any of that quarter's metrics.
        if values["revenue"] <= 0:
            previous = None
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
        historical_financials: list[dict[str, Any]] | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.sleeper = sleeper
        self._request_lock = Lock()
        self._store_lock = Lock()
        self._last_request_started = 0.0
        self._thread_state = local()
        self._reported_failure_diagnostics: set[str] = set()
        self._history_by_company: dict[str, list[dict[str, Any]]] = {}
        for row in historical_financials or []:
            company_id = str(row.get("company_id") or "")
            if company_id:
                self._history_by_company.setdefault(company_id, []).append(row)

    def _failure_diagnostic(self, error: Exception) -> str:
        """Expose a credential-safe diagnostic with enough location to repair code.

        Provider payloads and request values remain hidden. For local Python
        failures, the final traceback frame identifies the broken code path
        without logging filing contents, credentials, or rejected values.
        """
        if isinstance(error, EarningsStoreError):
            return str(error)
        frames = traceback.extract_tb(error.__traceback__)
        if not frames:
            return type(error).__name__
        frame = frames[-1]
        return f"{type(error).__name__}:{frame.name}:{frame.lineno}"

    def _archive(self, receipt: str) -> bytes:
        # Start requests at a bounded cadence while allowing slow network
        # responses to overlap. Each thread owns its requests.Session.
        with self._request_lock:
            elapsed = time.monotonic() - self._last_request_started
            remaining = self.request_interval_seconds - elapsed
            if remaining > 0:
                self.sleeper(remaining)
            self._last_request_started = time.monotonic()
        client = getattr(self._thread_state, "client", None)
        if client is None:
            client = OpenDartClient(self.client.api_key, timeout=self.client.timeout)
            self._thread_state.client = client
        response = client.fetch_filing_archive(receipt)
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
        def fetch_and_parse(job: dict[str, Any]):
            report_code = str(job["report_code"])
            metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
            receipt = str(metadata.get("receipt_no") or "").strip()
            parsed = parse_legacy_filing_archive(
                self._archive(receipt), report_code=report_code,
            )
            return report_code, parsed

        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as executor:
            futures = {executor.submit(fetch_and_parse, job): str(job["report_code"]) for job in jobs}
            for future in as_completed(futures):
                report_code = futures[future]
                try:
                    parsed_code, parsed = future.result()
                    statements[parsed_code] = parsed
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
                quality_issues: list[str] = []
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
                    quality_issues = validate_canonical_quarter(
                        quarter,
                        self._history_by_company.get(str(job["company_id"]), []),
                    )
                    outcome = "review_required" if quality_issues else "complete"
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
                        "financial_method": "legacy_dart_document_archive_v5",
                        "parse_error": parse_errors.get(report_code),
                        "quality_issues": quality_issues,
                    },
                }
                # requests.Session is not shared concurrently. Archive fetches
                # use thread-local clients; compact Supabase writes are
                # serialized through the worker's service-role session.
                with self._store_lock:
                    self.store.complete_open_dart_job(
                        job_id=int(job["id"]), filing=filing, quarter=quarter, outcome=outcome,
                    )
                if quarter is not None and outcome == "complete":
                    self._history_by_company.setdefault(str(job["company_id"]), []).append({
                        "company_id": str(job["company_id"]),
                        "fiscal_year": year,
                        **quarter,
                    })
                result[_outcome_counter_key(outcome)] += 1
            except Exception as error:
                diagnostic = self._failure_diagnostic(error)
                if diagnostic not in self._reported_failure_diagnostics:
                    self._reported_failure_diagnostics.add(diagnostic)
                    print(json.dumps({
                        "event": "legacy_dart_completion_failed",
                        "diagnostic": diagnostic,
                    }, ensure_ascii=False), flush=True)
                with self._store_lock:
                    self.store.fail_open_dart_job(job_id=int(job["id"]), error=diagnostic)
                result["failed"] += 1
        return result


def main() -> None:
    client = OpenDartClient.from_env()
    store = SupabaseEarningsStore.from_env()
    # The workflow exposes 2,500 as its documented upper bound. Keep the
    # worker consistent so a repair run can drain a large historical backlog
    # instead of silently stopping after 500 company-years.
    max_company_years = max(
        1, min(int(os.getenv("LEGACY_DART_MAX_COMPANY_YEARS", "100")), 2500)
    )
    interval = max(0.2, float(os.getenv("OPEN_DART_REQUEST_INTERVAL_SECONDS", "0.3")))
    company_workers = max(
        1, min(int(os.getenv("LEGACY_DART_COMPANY_WORKERS", "8")), 8)
    )
    worker = LegacyDartFinancialWorker(
        client, store, request_interval_seconds=interval,
        historical_financials=store.list_all_quarterly_financials(),
    )
    totals = {"company_years": 0, "completed": 0, "review_required": 0, "failed": 0}
    # Claim one year from different companies before each parallel wave. The
    # database claim function excludes companies that already have running
    # rows, preserving chronological context within a company while slow DART
    # archives from unrelated companies overlap safely.
    with ThreadPoolExecutor(max_workers=company_workers) as executor:
        while totals["company_years"] < max_company_years:
            claimed_batches: list[list[dict[str, Any]]] = []
            remaining = max_company_years - totals["company_years"]
            for _ in range(min(company_workers, remaining)):
                jobs = store.claim_open_dart_legacy_jobs()
                if not jobs:
                    break
                claimed_batches.append(jobs)
            if not claimed_batches:
                break
            futures = [executor.submit(worker.process_company_year, jobs) for jobs in claimed_batches]
            for future in as_completed(futures):
                batch = future.result()
                totals["company_years"] += 1
                for key, value in batch.items():
                    totals[key] += value
    useful = totals["completed"] + totals["review_required"]
    # Individual legacy archives can be malformed or temporarily unavailable.
    # Those jobs remain retryable; do not suppress downstream recalculation
    # after a productive batch. A run that made no useful progress is still a
    # hard failure and remains visible in GitHub Actions.
    ok = totals["failed"] == 0 or useful > 0
    print(json.dumps({"ok": ok, **totals}, ensure_ascii=False))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
