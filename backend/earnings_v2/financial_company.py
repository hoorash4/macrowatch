"""V2 backfill adapter for the existing seven-service FSC reader.

V2.5 owns the verified sector/account mappings. This adapter keeps V2's
deadline/error contract and merges only missing backfill metrics.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from earnings_v25.providers import (
    FinancialCompanyClient as ExistingFinancialCompanyClient,
    FinancialCompanySnapshot,
    ProviderError as ExistingProviderError,
    FINANCIAL_COMPANY_FUNCTION, REPORT_CODES, CONNECT_TIMEOUT, STANDARD_READ_TIMEOUT,
)
from .http import ExecutionDeadlineExceeded, bounded_request, safe_request_failure
from .models import FinancialFact
from .providers import ProviderError, _retryable_request_error


class FinancialCompanyClient(ExistingFinancialCompanyClient):
    def _source_request(self, payload: dict[str, Any], *, operation: str,
                        total_timeout: float = 30,
                        attempt_timeout: float = 12) -> dict[str, Any]:
        self.request_count += 1
        try:
            result = bounded_request(
                self.session, "POST",
                f"{self.supabase_url}/functions/v1/{FINANCIAL_COMPANY_FUNCTION}",
                provider="Financial Services Commission", operation=operation,
                headers={"Authorization": f"Bearer {self.internal_token}",
                         "apikey": self.service_key, "Content-Type": "application/json",
                         "X-Public-Data-API-Key": self.public_data_key},
                json=payload, total_timeout=total_timeout,
                attempt_timeout=attempt_timeout, connect_timeout=CONNECT_TIMEOUT, read_timeout=STANDARD_READ_TIMEOUT,
                on_retry=lambda attempt, reason, remaining: self._progress(
                    "provider_request_retry", provider="Financial Services Commission",
                    endpoint=operation, attempt=attempt, reason=reason,
                    remaining_budget_seconds=remaining),
            )
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            raise ProviderError(safe_request_failure("Financial Services Commission", operation, exc),
                                retryable=_retryable_request_error(exc)) from None
        if not isinstance(result, dict):
            raise ProviderError("Financial Services Commission returned invalid JSON")
        return result

    def quarter_financials(self, crno: str, year: int, quarter: int,
                           industry_code: str | None = None) -> list[FinancialCompanySnapshot]:
        try:
            snapshots = super().quarter_financials(crno, year, quarter, industry_code)
        except ExistingProviderError as exc:
            raise ProviderError(str(exc), retryable=exc.retryable) from None
        if any(item.crno != crno for item in snapshots):
            raise ProviderError("Financial Services Commission returned a different company")
        return snapshots


def merge_financial_company(fact: FinancialFact, snapshots: list[FinancialCompanySnapshot],
                            year: int, quarter: int,
                            previous_fact: FinancialFact | None) -> FinancialFact:
    fields = ("top_line", "operating_income", "net_income")
    if fact.fully_complete:
        return fact
    existing = any(getattr(fact, field) is not None for field in fields)
    candidates = [s for s in snapshots if s.report_code == REPORT_CODES[quarter]]
    matching = [s for s in candidates if s.consolidation_scope == fact.consolidation_scope]
    snapshot = matching[0] if matching else None
    if snapshot is None and not existing:
        snapshot = next((s for scope in ("CFS", "OFS") for s in candidates
                         if s.consolidation_scope == scope), None)
        if snapshot is None and len(candidates) == 1:
            snapshot = candidates[0]
    if snapshot is None or snapshot.currency != "KRW":
        return fact
    # Do not relabel previously collected foreign-currency metrics as KRW.
    if existing and fact.currency != "KRW":
        return fact
    changes: dict[str, Any] = {}
    for field in fields:
        if getattr(fact, field) is not None:
            continue
        cumulative = getattr(snapshot, f"{field}_cumulative")
        standalone = getattr(snapshot, f"{field}_standalone")
        if standalone is None and cumulative is not None:
            previous = (getattr(previous_fact, f"source_{field}_cumulative")
                        if previous_fact is not None and previous_fact.source_currency == "KRW"
                        and previous_fact.fiscal_year == year
                        and previous_fact.fiscal_quarter == quarter - 1 else None)
            standalone = cumulative - previous if previous is not None else cumulative / Decimal(quarter)
        if standalone is not None:
            changes[field] = standalone
            changes[f"source_{field}_cumulative"] = cumulative
    if not changes:
        return fact
    receipt = f"financial_services_commission:{snapshot.crno}:{year}:{snapshot.report_code}"
    changes.update(
        consolidation_scope=snapshot.consolidation_scope if not existing and snapshot.consolidation_scope else fact.consolidation_scope,
        source="mixed" if existing else "financial_services_commission",
        source_filing_id=f"mixed:{fact.source_filing_id}|{receipt}" if existing else receipt,
        filing_date=fact.filing_date if existing else date(year, quarter * 3, 31 if quarter in (1, 4) else 30),
        currency="KRW", source_currency="KRW",
    )
    resolved = fact.with_changes(**changes)
    return resolved.with_changes(is_pending=not resolved.fully_complete)

