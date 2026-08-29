"""Process resumable OpenDART financial-period jobs in compatible batches."""

from __future__ import annotations

from collections import Counter, defaultdict
from calendar import monthrange
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
import os
import re
import time
from typing import Any, Callable, Iterable

from earnings.open_dart import OpenDartApiError, OpenDartClient, OpenDartResponse
from earnings.dart_statement_revenue import (
    DartRevenueDerivationError,
    derive_gross_revenue_from_archive,
)
from earnings.open_dart_parser import (
    DartAccountFact,
    REPORT_QUARTERS,
    REQUIRED_METRICS,
    parse_account_rows,
    select_preferred_accounts,
    standalone_quarter_value,
)
from earnings.supabase_rest import SupabaseEarningsStore


PREVIOUS_REPORT_CODE = {"11012": "11013", "11014": "11012", "11011": "11014"}
FILING_KIND = {"11013": "q1", "11012": "half_year", "11014": "q3", "11011": "annual"}
REPORT_END_MONTH = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def reporting_period_bounds(
    business_year: int,
    report_code: str,
    report_name: str | None = None,
) -> tuple[date, date]:
    """Recover quarter bounds omitted by OpenDART's multi-company endpoint.

    Filing discovery preserves the official report name, whose ``(YYYY.MM)``
    suffix identifies the reporting period.  The report code is the official
    fallback for older queued jobs that predate that metadata field.
    """
    year = int(business_year)
    month = REPORT_END_MONTH[report_code]
    match = re.search(r"\((\d{4})\.(03|06|09|12)\)", str(report_name or ""))
    if match:
        year, month = (int(value) for value in match.groups())
    quarter_start_month = month - 2
    return date(year, quarter_start_month, 1), date(year, month, monthrange(year, month)[1])


def attach_reporting_period(
    facts: Iterable[DartAccountFact],
    *,
    business_year: int,
    report_code: str,
    report_name: str | None = None,
) -> list[DartAccountFact]:
    """Fill only dates absent from multi-company rows; preserve supplied dates."""
    period_start, period_end = reporting_period_bounds(business_year, report_code, report_name)
    return [replace(
        fact,
        period_start=fact.period_start or period_start,
        period_end=fact.period_end or period_end,
    ) for fact in facts]


def build_canonical_quarter(
    selected: dict[str, DartAccountFact],
    previous_selected: dict[str, DartAccountFact] | None,
    *,
    filed_on: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build one non-mixed canonical quarter, preserving valid partial metrics."""
    missing = [metric for metric in REQUIRED_METRICS if metric not in selected]
    if not selected:
        return None, missing

    scopes = {fact.consolidation_scope for fact in selected.values()}
    if len(scopes) != 1:
        return None, list(REQUIRED_METRICS)

    values: dict[str, Decimal] = {}
    for metric, fact in selected.items():
        previous = (previous_selected or {}).get(metric)
        previous_cumulative = None
        if previous and previous.consolidation_scope == fact.consolidation_scope:
            previous_cumulative = previous.cumulative_amount
        value = standalone_quarter_value(fact, previous_cumulative=previous_cumulative)
        if value is None and metric in REQUIRED_METRICS:
            missing.append(metric)
        elif value is not None:
            values[metric] = value
    missing = sorted(set(missing))
    if not values:
        return None, missing

    representative = next(
        selected[metric] for metric in REQUIRED_METRICS if metric in selected
    )
    period_end = representative.period_end
    if period_end is None:
        return None, list(REQUIRED_METRICS)
    # Canonical rows always describe the standalone quarter, even when the
    # provider's source field is cumulative from the beginning of the year.
    period_start = date(period_end.year, period_end.month - 2, 1)
    previous_periods = [fact.period_end for fact in (previous_selected or {}).values() if fact.period_end]
    if representative.fiscal_quarter > 1 and previous_periods:
        period_start = max(previous_periods) + timedelta(days=1)

    currencies = {fact.currency for fact in selected.values() if fact.currency}
    if len(currencies) > 1:
        return None, list(REQUIRED_METRICS)
    currency = next(iter(currencies), "KRW")
    return {
        "fiscal_quarter": representative.fiscal_quarter,
        "market_year": representative.business_year,
        "market_quarter": representative.fiscal_quarter,
        "period_start": _date_text(period_start),
        "period_end": period_end.isoformat(),
        **{metric: _decimal_text(values.get(metric)) for metric in REQUIRED_METRICS},
        "currency": currency,
        "consolidation_scope": next(iter(scopes)),
        "missing_metrics": missing,
        "source_updated_at": f"{filed_on}T00:00:00+09:00",
    }, missing


class OpenDartFinancialWorker:
    """Keep provider transport separate from atomic database persistence."""

    def __init__(
        self,
        client: OpenDartClient,
        store: SupabaseEarningsStore,
        *,
        request_interval_seconds: float = 0.2,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.store = store
        self.request_interval_seconds = max(0.0, request_interval_seconds)
        self.sleeper = sleeper
        self._has_requested = False
        self.error_counts: Counter[str] = Counter()

    def _record_error(self, error: Exception) -> None:
        """Keep actionable diagnostics while force-redacting both credentials."""
        message = " ".join(str(error).split())[:300]
        for secret in (self.client.api_key, self.store.service_role_key):
            if secret:
                message = message.replace(secret, "[redacted]")
        self.error_counts[f"{type(error).__name__}: {message}"] += 1

    def _request(self, callback: Callable[[], OpenDartResponse]) -> OpenDartResponse:
        if self._has_requested and self.request_interval_seconds:
            self.sleeper(self.request_interval_seconds)
        response = callback()
        self._has_requested = True
        return response

    def _augment_company_facts(
        self,
        corp_code: str,
        business_year: int,
        report_code: str,
        facts: list[DartAccountFact],
    ) -> list[DartAccountFact]:
        """Use full CFS then OFS until core profit facts support a safe result."""
        for scope in ("CFS", "OFS"):
            selected = select_preferred_accounts(facts).get(corp_code, {})
            if all(metric in selected for metric in REQUIRED_METRICS) or all(
                metric in selected for metric in ("operating_income", "net_income")
            ):
                break
            response = self._request(lambda scope=scope: self.client.fetch_single_all_accounts(
                corp_code, business_year, report_code, scope
            ))
            facts.extend(parse_account_rows(response.payload))
        return facts

    def _derive_missing_revenue(
        self,
        corp_code: str,
        facts: list[DartAccountFact],
    ) -> list[DartAccountFact]:
        """Append one reconciled financial-company revenue fact when needed."""
        selected = select_preferred_accounts(facts).get(corp_code, {})
        if "revenue" in selected or "operating_income" not in selected:
            return facts
        operating = selected["operating_income"]
        receipt = operating.receipt_number or next(
            (fact.receipt_number for fact in selected.values() if fact.receipt_number), ""
        )
        if not receipt or operating.consolidation_scope not in {"CFS", "OFS"}:
            return facts
        try:
            full_accounts = self._request(
                lambda: self.client.fetch_single_all_accounts(
                    corp_code,
                    operating.business_year,
                    operating.report_code,
                    operating.consolidation_scope,
                )
            )
            income_rows = [
                {
                    "ord": row.get("ord"),
                    "account_id": row.get("account_id"),
                    "account_nm": row.get("account_nm"),
                    "thstrm_amount": row.get("thstrm_amount"),
                    "thstrm_add_amount": row.get("thstrm_add_amount"),
                }
                for row in full_accounts.rows
                if str(row.get("fs_div") or "").upper()
                == operating.consolidation_scope
                and str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
            ]
            print(json.dumps({
                "event": "dart_full_income_account_sample",
                "receipt_no": receipt,
                "rows": income_rows[:120],
            }, ensure_ascii=False), flush=True)
        except OpenDartApiError as error:
            print(json.dumps({
                "event": "dart_full_income_accounts_unavailable",
                "receipt_no": receipt,
                "status": error.status,
            }, ensure_ascii=False), flush=True)
        try:
            archive_response = self._request(
                lambda: self.client.fetch_filing_archive(receipt)
            )
            amounts = derive_gross_revenue_from_archive(
                archive_response.content,
                operating_current=operating.current_amount,
                operating_cumulative=operating.cumulative_amount,
                consolidation_scope=operating.consolidation_scope,
            )
        except OpenDartApiError as error:
            if error.status != "014":
                raise
            print(json.dumps({
                "event": "dart_revenue_archive_unavailable",
                "receipt_no": receipt,
                "status": error.status,
            }, ensure_ascii=False), flush=True)
            return facts
        except DartRevenueDerivationError as error:
            # A layout that cannot be reconciled remains an explicit partial
            # quarter. Never guess a top line or retry a deterministic mismatch.
            print(json.dumps({
                "event": "dart_revenue_derivation_skipped",
                "receipt_no": receipt,
                "reason": str(error),
            }, ensure_ascii=False), flush=True)
            return facts
        if amounts.current_revenue is None and amounts.cumulative_revenue is None:
            return facts
        facts.append(replace(
            operating,
            metric="revenue",
            account_id="derived-gross-operating-revenue",
            account_name="총영업수익(공시 트리 합산)",
            current_amount=amounts.current_revenue,
            cumulative_amount=amounts.cumulative_revenue,
        ))
        return facts

    def _fetch_previous(
        self,
        corp_codes: list[str],
        business_year: int,
        report_code: str,
    ) -> dict[str, list[DartAccountFact]]:
        previous_code = PREVIOUS_REPORT_CODE.get(report_code)
        if not previous_code:
            return {}
        response = self._request(lambda: self.client.fetch_multi_accounts(
            corp_codes, business_year, previous_code
        ))
        grouped: dict[str, list[DartAccountFact]] = defaultdict(list)
        for fact in parse_account_rows(response.payload):
            grouped[fact.corp_code].append(fact)
        return dict(grouped)

    def process_batch(self, jobs: list[dict[str, Any]]) -> dict[str, int]:
        if not jobs:
            return {"completed": 0, "review_required": 0, "no_data": 0, "failed": 0}
        business_year = int(jobs[0]["business_year"])
        report_code = str(jobs[0]["report_code"])
        if any(int(job["business_year"]) != business_year or str(job["report_code"]) != report_code
               for job in jobs):
            raise ValueError("A financial batch must contain one business year and report code.")
        corp_codes = [str(job["corp_code"]) for job in jobs]
        result = {"completed": 0, "review_required": 0, "no_data": 0, "failed": 0}

        try:
            response = self._request(lambda: self.client.fetch_multi_accounts(
                corp_codes, business_year, report_code
            ))
        except Exception as error:
            self._record_error(error)
            for job in jobs:
                self.store.fail_open_dart_job(job_id=int(job["id"]), error=str(error))
                result["failed"] += 1
            return result

        current_by_company: dict[str, list[DartAccountFact]] = defaultdict(list)
        for fact in parse_account_rows(response.payload):
            current_by_company[fact.corp_code].append(fact)

        # Q4 always needs 9M cumulative data. Interim reports usually expose a
        # reliable three-month amount, so avoid an extra provider call unless
        # at least one returned metric actually needs cumulative subtraction.
        preliminary = select_preferred_accounts(
            fact for company_facts in current_by_company.values() for fact in company_facts
        )
        needs_previous_batch = report_code == "11011" or any(
            standalone_quarter_value(fact) is None
            for selected in preliminary.values()
            for metric, fact in selected.items() if metric in REQUIRED_METRICS
        )
        try:
            if needs_previous_batch:
                previous_by_company = self._fetch_previous(
                    corp_codes, business_year, report_code
                )
            else:
                previous_by_company = {}
        except Exception as error:
            self._record_error(error)
            for job in jobs:
                self.store.fail_open_dart_job(job_id=int(job["id"]), error=str(error))
                result["failed"] += 1
            return result

        for job in jobs:
            corp_code = str(job["corp_code"])
            try:
                current_facts = self._augment_company_facts(
                    corp_code, business_year, report_code,
                    list(current_by_company.get(corp_code, [])),
                )
                if not current_facts:
                    self.store.complete_open_dart_job(
                        job_id=int(job["id"]), filing={}, quarter=None, outcome="no_data",
                    )
                    result["no_data"] += 1
                    continue

                metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
                current_facts = attach_reporting_period(
                    current_facts,
                    business_year=business_year,
                    report_code=report_code,
                    report_name=metadata.get("report_name"),
                )
                current_facts = self._derive_missing_revenue(corp_code, current_facts)
                selected = select_preferred_accounts(current_facts).get(corp_code, {})
                previous_code = PREVIOUS_REPORT_CODE.get(report_code)
                previous_facts = attach_reporting_period(
                    list(previous_by_company.get(corp_code, [])),
                    business_year=business_year,
                    report_code=previous_code,
                ) if previous_code else []
                needs_previous = any(
                    standalone_quarter_value(fact) is None
                    for metric, fact in selected.items() if metric in REQUIRED_METRICS
                )
                if needs_previous and report_code in PREVIOUS_REPORT_CODE:
                    if not previous_by_company:
                        previous_by_company = self._fetch_previous(
                            corp_codes, business_year, report_code
                        )
                    previous_facts = self._augment_company_facts(
                        corp_code, business_year, PREVIOUS_REPORT_CODE[report_code],
                        previous_facts,
                    )
                    previous_facts = attach_reporting_period(
                        previous_facts,
                        business_year=business_year,
                        report_code=PREVIOUS_REPORT_CODE[report_code],
                    )
                    previous_facts = self._derive_missing_revenue(corp_code, previous_facts)
                previous_selected = select_preferred_accounts(previous_facts).get(corp_code, {})

                receipt = str(metadata.get("receipt_no") or "").strip()
                if not receipt:
                    receipt = next((fact.receipt_number for fact in current_facts if fact.receipt_number), "")
                filed_on = str(metadata.get("filed_on") or "").strip()
                if not filed_on and len(receipt) >= 8 and receipt[:8].isdigit():
                    filed_on = f"{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}"
                period_end = next((fact.period_end for fact in selected.values() if fact.period_end), None)
                if not receipt or not filed_on or period_end is None:
                    raise ValueError("OpenDART response lacks filing identity or reporting period.")

                quarter, missing = build_canonical_quarter(
                    selected, previous_selected, filed_on=filed_on
                )
                outcome = "complete" if quarter is not None and not missing else "review_required"
                filing = {
                    "source_filing_id": receipt,
                    "filing_kind": FILING_KIND[report_code],
                    "fiscal_quarter": REPORT_QUARTERS[report_code],
                    "market_year": business_year,
                    "market_quarter": REPORT_QUARTERS[report_code],
                    "period_start": _date_text(next((fact.period_start for fact in selected.values()
                                                     if fact.period_start), None)),
                    "period_end": period_end.isoformat(),
                    "filing_date": filed_on,
                    "is_correction": bool(metadata.get("is_correction")),
                    "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                    "metadata": {
                        "report_name": metadata.get("report_name"),
                        "missing_metrics": missing,
                        "revenue_method": (
                            "dart_statement_gross_operating_revenue_v2"
                            if selected.get("revenue") is not None
                            and selected["revenue"].account_id
                            == "derived-gross-operating-revenue"
                            else "open_dart_account"
                        ),
                    },
                }
                self.store.complete_open_dart_job(
                    job_id=int(job["id"]),
                    filing=filing,
                    quarter=quarter, outcome=outcome,
                )
                result["completed" if outcome == "complete" else outcome] += 1
            except Exception as error:
                self._record_error(error)
                self.store.fail_open_dart_job(job_id=int(job["id"]), error=str(error))
                result["failed"] += 1
        return result


def main() -> None:
    client = OpenDartClient.from_env()
    store = SupabaseEarningsStore.from_env()
    max_batches = max(1, min(int(os.getenv("OPEN_DART_MAX_BATCHES", "5")), 50))
    interval = max(0.0, float(os.getenv("OPEN_DART_REQUEST_INTERVAL_SECONDS", "0.2")))
    worker = OpenDartFinancialWorker(client, store, request_interval_seconds=interval)
    totals = {"batches": 0, "completed": 0, "review_required": 0, "no_data": 0, "failed": 0}
    for _ in range(max_batches):
        jobs = store.claim_open_dart_jobs(limit=100)
        if not jobs:
            break
        batch_result = worker.process_batch(jobs)
        totals["batches"] += 1
        for key, value in batch_result.items():
            totals[key] += value
        print(json.dumps({
            "batch": totals["batches"],
            "jobs": len(jobs),
            **batch_result,
        }, ensure_ascii=False), flush=True)
    summary = {
        "ok": totals["failed"] == 0,
        **totals,
        "errors": dict(worker.error_counts.most_common(10)),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if totals["failed"]:
        # GitHub exposes this deliberately secret-redacted summary through the
        # check annotation API, so failures can be diagnosed without opening
        # or downloading the full workflow log.
        annotation = json.dumps(summary, ensure_ascii=False).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=OpenDART financial worker::{annotation}", flush=True)
        raise SystemExit(1)
    # Expose only aggregate counts so a live success can be verified through
    # the check API without downloading credential-bearing workflow logs.
    annotation = json.dumps(summary, ensure_ascii=False).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::notice title=OpenDART financial worker::{annotation}", flush=True)


if __name__ == "__main__":
    main()
