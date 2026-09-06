from __future__ import annotations

from datetime import date, timedelta

from .pipeline import (
    MARKETS, USEarningsAutomaticPipeline, fact_from_row, market_period,
    previous_market_period,
)
from .providers import ProviderError
from .transform import extract_new_sec_facts


def _all_financial_accessions(payload: dict) -> set[str]:
    result: set[str] = set()
    for taxonomy in payload.get("facts", {}).values() if isinstance(payload.get("facts"), dict) else ():
        if not isinstance(taxonomy, dict):
            continue
        for fact in taxonomy.values():
            if not isinstance(fact, dict):
                continue
            for values in fact.get("units", {}).values() if isinstance(fact.get("units"), dict) else ():
                if not isinstance(values, list):
                    continue
                for row in values:
                    if isinstance(row, dict) and str(row.get("form") or "").upper() in {"10-Q", "10-K", "10-Q/A", "10-K/A"}:
                        accession = str(row.get("accn") or "")
                        if accession:
                            result.add(accession)
    return result


def _select_backfill_fact(candidates: list):
    """Prefer a complete physical-period fact over a later malformed comparative."""
    return max(candidates, key=lambda fact: (fact.fully_complete, fact.period_end, fact.filing_date))


class USEarningsBackfillPipeline(USEarningsAutomaticPipeline):
    """Automatic collector's SEC interpretation, with authoritative period replacement."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._backfill_facts: dict[str, list] = {}
        self._current_sec_ciks: set[str] | None = None

    def freeze_universe_period(self, year: int, quarter: int, *, write: bool = True) -> dict:
        """Persist one exact historical index membership only after both 100-company sets validate."""
        reference_date = date(year, quarter * 3, 31 if quarter in {1, 4} else 30)
        directory = self.sec.ticker_directory()
        historical_ciks: dict[str, set[str]] = {}
        for row in self.repository.us_active_companies(year):
            ticker = str(row.get("ticker") or "").strip().upper()
            cik = str(row.get("cik") or "").strip()
            if ticker and cik:
                historical_ciks.setdefault(ticker, set()).add(cik)
        # Reuse only unambiguous mappings already validated and persisted by a
        # neighbouring historical period. Reused tickers with multiple CIKs
        # remain unresolved and must go through the period-scoped SEC search.
        historical_directory = {
            ticker: next(iter(ciks)) for ticker, ciks in historical_ciks.items() if len(ciks) == 1
        }
        sp100 = self.constituents.sp100_historical(reference_date, directory, historical_directory)
        nasdaq100 = self.constituents.nasdaq100(reference_date, directory, historical_directory)
        by_market = {"us_sp100": sp100, "us_nasdaq100": nasdaq100}
        if write:
            securities = [*sp100, *nasdaq100]
            self.persist_universe_securities(securities, historical=True)
            for market, rows in by_market.items():
                self.repository.save_us_universe(market, year, quarter, rows)
            self.repository.save_us_state("universe_backfill", "ready", {"period": f"{year}Q{quarter}"})
        return {
            "period": f"{year}Q{quarter}", "write": write, "status": "ready",
            "markets": {market: len(rows) for market, rows in by_market.items()},
            "requests": {"index_sources": self.constituents.request_count, "sec": self.sec.request_count},
        }

    def _company_facts(self, member) -> list:
        cached = self._backfill_facts.get(member.company_id)
        if cached is not None:
            return cached
        payload = self.sec.company_facts(member.cik)
        facts = extract_new_sec_facts(member.company_id, payload, _all_financial_accessions(payload))
        self._backfill_facts[member.company_id] = facts
        return facts

    def _delisted_carry_forward(self, member, year: int, quarter: int):
        """Use the agreed prior-quarter proxy only for a confirmed Form 25 exit."""
        if self._current_sec_ciks is None:
            self._current_sec_ciks = {
                cik.zfill(10) for _, _, cik in self.sec.company_ticker_rows()
            }
        if member.cik.zfill(10) in self._current_sec_ciks:
            return None
        start = date(year, (quarter - 1) * 3 + 1, 1)
        end = date(year, quarter * 3, 31 if quarter in {1, 4} else 30)
        applicable = next((
            filed for filed in self.sec.delisting_dates(member.cik)
            if start <= filed <= end + timedelta(days=120)
        ), None)
        if applicable is None:
            return None
        prior_market = previous_market_period(year, quarter)
        prior = []
        for row in self.repository.company_history([member.company_id]):
            try:
                if (int(row["market_year"]), int(row["market_quarter"])) == prior_market:
                    fact = fact_from_row(row)
                    if fact.fully_complete:
                        prior.append(fact)
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
        if not prior:
            return None
        source = max(prior, key=lambda fact: fact.period_end)
        return source.with_changes(
            fiscal_year=year, fiscal_quarter=quarter,
            period_start=start, period_end=end,
            source_filing_id=f"carry-forward-form25-{applicable.isoformat()}",
            filing_date=applicable, is_pending=False,
        )

    def backfill_period(
        self, year: int, quarter: int, *, write: bool = True,
        strict_provider_errors: bool = False,
    ) -> dict:
        rows = [member for market in MARKETS for member in self.repository.us_universe(market, year, quarter)]
        unique = {member.company_id: member for member in rows}
        if not unique:
            raise ValueError(f"No frozen U.S. universe exists for {year}Q{quarter}")
        changed = []
        issues = []
        for member in unique.values():
            if not member.cik:
                issues.append({"company": member.company_name, "reason": "SEC CIK missing"})
                continue
            try:
                candidates = [
                    fact for fact in self._company_facts(member)
                    if market_period(fact.period_end) == (year, quarter)
                ]
            except ProviderError as exc:
                if strict_provider_errors:
                    raise ProviderError(f"{member.company_name}: {exc}") from exc
                issues.append({"company": member.company_name, "reason": str(exc)})
                continue
            if not candidates:
                try:
                    carry_forward = self._delisted_carry_forward(member, year, quarter)
                except ProviderError as exc:
                    if strict_provider_errors:
                        raise ProviderError(f"{member.company_name}: {exc}") from exc
                    carry_forward = None
                if carry_forward is None:
                    issues.append({"company": member.company_name, "reason": "No SEC financial fact mapped to market period"})
                    continue
                changed.append(carry_forward)
                continue
            selected = _select_backfill_fact(candidates)
            changed.append(selected)
            if selected.is_pending:
                issues.append({"company": member.company_name, "reason": "SEC financial fact is incomplete"})
        if write and changed:
            self.repository.replace_company_quarters_for_backfill(fact.db_row() for fact in changed)
            self.recalculate_market_period(year, quarter)
        status = "incomplete" if issues else "ready"
        result = {"period": f"{year}Q{quarter}", "write": write, "status": status,
                  "universe_companies": len(unique), "replaced_company_quarters": len(changed),
                  "missing_or_pending_companies": len(issues),
                  "issues": issues, "requests": {"sec": self.sec.request_count}}
        if write:
            self.repository.save_us_state("backfill", status, {"period": result["period"]})
        return result

