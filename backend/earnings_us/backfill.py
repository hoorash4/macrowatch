from __future__ import annotations

from datetime import date, timedelta

from .constituents import _name_match_score
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
        self._backfill_facts: dict[tuple[str, str], list] = {}
        self._historical_companies: list[dict] | None = None
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
        return self._company_facts_for_cik(member.company_id, member.cik)

    def _company_facts_for_cik(self, company_id: str, cik: str) -> list:
        key = (company_id, cik.zfill(10))
        cached = self._backfill_facts.get(key)
        if cached is not None:
            return cached
        payload = self.sec.company_facts(cik)
        facts = extract_new_sec_facts(company_id, payload, _all_financial_accessions(payload))
        self._backfill_facts[key] = facts
        return facts

    def _historical_ticker_candidates(self, member, year: int, quarter: int) -> list:
        """Read exact-period facts from the same issuer's predecessor or successor CIK."""
        if self._historical_companies is None:
            self._historical_companies = self.repository.us_active_companies(2016)
        current_cik = member.cik.zfill(10)
        alternative_ciks: set[str] = set()
        for row in self._historical_companies:
            cik = str(row.get("cik") or "").strip().zfill(10)
            ticker = str(row.get("ticker") or "").strip().upper()
            name = str(row.get("company_name") or "").strip()
            same_ticker = bool(member.ticker and ticker and member.ticker.upper() == ticker)
            same_name = bool(name and _name_match_score(member.company_name, name) >= 100)
            if cik and cik != current_cik and (same_ticker or same_name):
                alternative_ciks.add(cik)
        result = []
        for cik in sorted(alternative_ciks):
            result.extend(
                fact for fact in self._company_facts_for_cik(member.company_id, cik)
                if market_period(fact.period_end) == (year, quarter)
            )
        return result

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
        next_fiscal_year = source.fiscal_year + (1 if source.fiscal_quarter == 4 else 0)
        next_fiscal_quarter = 1 if source.fiscal_quarter == 4 else source.fiscal_quarter + 1
        return source.with_changes(
            fiscal_year=next_fiscal_year, fiscal_quarter=next_fiscal_quarter,
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
            if not candidates or not any(fact.fully_complete for fact in candidates):
                try:
                    candidates.extend(self._historical_ticker_candidates(member, year, quarter))
                except ProviderError as exc:
                    if strict_provider_errors:
                        raise ProviderError(f"{member.company_name}: {exc}") from exc
            if not candidates or not any(fact.fully_complete for fact in candidates):
                try:
                    candidates.extend(self._six_k_candidates(member.company_id, member.cik, year, quarter))
                except ProviderError as exc:
                    if strict_provider_errors:
                        raise ProviderError(f"{member.company_name}: {exc}") from exc
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
        if write:
            # A forced backfill treats the whole requested U.S. market period as
            # untrusted. Clear every frozen-universe row first so a newly rejected
            # source cannot leave a stale value from an earlier parser run behind.
            self.repository.clear_us_backfill_period(year, quarter)
            if changed:
                self.repository.upsert_company_quarters(fact.db_row() for fact in changed)
            self.recalculate_market_period(year, quarter)
        status = "incomplete" if issues else "ready"
        result = {"period": f"{year}Q{quarter}", "write": write, "status": status,
                  "universe_companies": len(unique), "replaced_company_quarters": len(changed),
                  "missing_or_pending_companies": len(issues),
                  "issues": issues, "requests": {"sec": self.sec.request_count}}
        if write:
            self.repository.save_us_state("backfill", status, {"period": result["period"]})
        return result

