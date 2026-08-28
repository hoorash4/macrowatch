"""Process resumable OpenDART financial-period jobs in compatible batches."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
import time
from typing import Any, Callable, Iterable

from earnings.open_dart import OpenDartClient, OpenDartResponse
from earnings.open_dart_parser import (
    ACCOUNT_ID_PRIORITIES,
    DartAccountFact,
    REPORT_QUARTERS,
    parse_account_rows,
    select_preferred_accounts,
    standalone_quarter_value,
)
from earnings.supabase_rest import SupabaseEarningsStore


REQUIRED_METRICS = tuple(ACCOUNT_ID_PRIORITIES)
PREVIOUS_REPORT_CODE = {"11012": "11013", "11014": "11012", "11011": "11014"}
FILING_KIND = {"11013": "q1", "11012": "half_year", "11014": "q3", "11011": "annual"}


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def batch_request_key(business_year: int, report_code: str, corp_codes: Iterable[str]) -> str:
    return f"{business_year}:{report_code}:{','.join(sorted(set(corp_codes)))}"


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def facts_for_storage(
    facts: Iterable[DartAccountFact],
    payload_ids: dict[str, str],
) -> list[dict[str, Any]]:
    """Preserve current-period and cumulative source fields as separate facts."""
    rows: list[dict[str, Any]] = []
    for fact in facts:
        if fact.period_end is None:
            continue
        fields = (
            ("thstrm_amount", fact.current_amount, "fy" if fact.fiscal_quarter == 4 else "quarter"),
            ("thstrm_add_amount", fact.cumulative_amount, "fy" if fact.fiscal_quarter == 4 else "ytd"),
        )
        for source_field, value, value_kind in fields:
            if value is None:
                continue
            rows.append({
                "metric": fact.metric,
                "source_account_id": fact.account_id,
                "source_account_name": fact.account_name,
                "statement_type": fact.statement_type,
                "consolidation_scope": fact.consolidation_scope,
                "period_start": _date_text(fact.period_start),
                "period_end": fact.period_end.isoformat(),
                "value_kind": value_kind,
                "value": _decimal_text(value),
                "currency": fact.currency,
                "source_field": source_field,
                "source_row_key": f"{fact.source_row_key}:{source_field}",
                "raw_row": fact.raw_row,
                "source_payload_id": payload_ids.get(fact.source_row_key),
            })
    return rows


def build_canonical_quarter(
    selected: dict[str, DartAccountFact],
    previous_selected: dict[str, DartAccountFact] | None,
    *,
    filed_on: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build one complete, non-mixed canonical quarter or return missing metrics."""
    missing = [metric for metric in REQUIRED_METRICS if metric not in selected]
    if missing:
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
        if value is None:
            missing.append(metric)
        else:
            values[metric] = value
    if missing:
        return None, sorted(set(missing))

    representative = selected["revenue"]
    period_end = representative.period_end
    if period_end is None:
        return None, list(REQUIRED_METRICS)
    period_start = representative.period_start
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
        **{metric: _decimal_text(values[metric]) for metric in REQUIRED_METRICS},
        "currency": currency,
        "consolidation_scope": next(iter(scopes)),
        "missing_metrics": [],
        "source_updated_at": f"{filed_on}T00:00:00+09:00",
    }, []


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

    def _save_response(self, operation: str, request_key: str, response: OpenDartResponse) -> str:
        return self.store.save_source_payload(
            operation=operation,
            request_key=request_key,
            request_params=response.request_params,
            payload_sha256=canonical_payload_hash(response.payload),
            payload=response.payload,
        )

    def _augment_company_facts(
        self,
        corp_code: str,
        business_year: int,
        report_code: str,
        facts: list[DartAccountFact],
        payload_ids: dict[str, str],
    ) -> list[DartAccountFact]:
        """Use full CFS then OFS only until one complete scope exists."""
        for scope in ("CFS", "OFS"):
            selected = select_preferred_accounts(facts).get(corp_code, {})
            if len(selected) == len(REQUIRED_METRICS):
                break
            response = self._request(lambda scope=scope: self.client.fetch_single_all_accounts(
                corp_code, business_year, report_code, scope
            ))
            payload_id = self._save_response(
                "financial_accounts_full",
                f"{business_year}:{report_code}:{corp_code}:{scope}",
                response,
            )
            parsed = parse_account_rows(response.payload)
            facts.extend(parsed)
            payload_ids.update({fact.source_row_key: payload_id for fact in parsed})
        return facts

    def _fetch_previous(
        self,
        corp_codes: list[str],
        business_year: int,
        report_code: str,
    ) -> tuple[dict[str, list[DartAccountFact]], dict[str, str]]:
        previous_code = PREVIOUS_REPORT_CODE.get(report_code)
        if not previous_code:
            return {}, {}
        response = self._request(lambda: self.client.fetch_multi_accounts(
            corp_codes, business_year, previous_code
        ))
        payload_id = self._save_response(
            "financial_accounts_previous",
            batch_request_key(business_year, previous_code, corp_codes),
            response,
        )
        grouped: dict[str, list[DartAccountFact]] = defaultdict(list)
        payload_ids: dict[str, str] = {}
        for fact in parse_account_rows(response.payload):
            grouped[fact.corp_code].append(fact)
            payload_ids[fact.source_row_key] = payload_id
        return dict(grouped), payload_ids

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
            main_payload_id = self._save_response(
                "financial_accounts_multi",
                batch_request_key(business_year, report_code, corp_codes),
                response,
            )
        except Exception as error:
            self._record_error(error)
            for job in jobs:
                self.store.fail_open_dart_job(job_id=int(job["id"]), error=str(error))
                result["failed"] += 1
            return result

        current_by_company: dict[str, list[DartAccountFact]] = defaultdict(list)
        current_payload_ids: dict[str, str] = {}
        for fact in parse_account_rows(response.payload):
            current_by_company[fact.corp_code].append(fact)
            current_payload_ids[fact.source_row_key] = main_payload_id

        # Q4 always needs 9M cumulative data. Interim reports usually expose a
        # reliable three-month amount, so avoid an extra provider call unless
        # at least one returned metric actually needs cumulative subtraction.
        preliminary = select_preferred_accounts(
            fact for company_facts in current_by_company.values() for fact in company_facts
        )
        needs_previous_batch = report_code == "11011" or any(
            standalone_quarter_value(fact) is None
            for selected in preliminary.values() for fact in selected.values()
        )
        try:
            if needs_previous_batch:
                previous_by_company, previous_payload_ids = self._fetch_previous(
                    corp_codes, business_year, report_code
                )
            else:
                previous_by_company, previous_payload_ids = {}, {}
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
                    list(current_by_company.get(corp_code, [])), current_payload_ids,
                )
                if not current_facts:
                    self.store.complete_open_dart_job(
                        job_id=int(job["id"]), source_payload_id=main_payload_id,
                        filing={}, facts=[], quarter=None, outcome="no_data",
                    )
                    result["no_data"] += 1
                    continue

                selected = select_preferred_accounts(current_facts).get(corp_code, {})
                previous_facts = list(previous_by_company.get(corp_code, []))
                needs_previous = any(
                    standalone_quarter_value(fact) is None for fact in selected.values()
                )
                if needs_previous and report_code in PREVIOUS_REPORT_CODE:
                    if not previous_by_company:
                        previous_by_company, previous_payload_ids = self._fetch_previous(
                            corp_codes, business_year, report_code
                        )
                    previous_facts = self._augment_company_facts(
                        corp_code, business_year, PREVIOUS_REPORT_CODE[report_code],
                        previous_facts, previous_payload_ids,
                    )
                previous_selected = select_preferred_accounts(previous_facts).get(corp_code, {})

                metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
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
                outcome = "complete" if quarter is not None else "review_required"
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
                    },
                }
                self.store.complete_open_dart_job(
                    job_id=int(job["id"]), source_payload_id=main_payload_id,
                    filing=filing,
                    facts=facts_for_storage(current_facts, current_payload_ids),
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


if __name__ == "__main__":
    main()
