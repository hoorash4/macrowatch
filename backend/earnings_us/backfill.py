from __future__ import annotations

from datetime import date

from .pipeline import MARKETS, USEarningsAutomaticPipeline, market_period
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


class USEarningsBackfillPipeline(USEarningsAutomaticPipeline):
    """Automatic collector's SEC interpretation, with authoritative period replacement."""

    def freeze_universe_period(self, year: int, quarter: int, *, write: bool = True) -> dict:
        """Persist one exact historical index membership only after both 100-company sets validate."""
        reference_date = date(year, quarter * 3, 31 if quarter in {1, 4} else 30)
        directory = self.sec.ticker_directory()
        sp100 = self.constituents.sp100_historical(reference_date, directory)
        nasdaq100 = self.constituents.nasdaq100(reference_date, directory)
        by_market = {"us_sp100": sp100, "us_nasdaq100": nasdaq100}
        if write:
            securities = [*sp100, *nasdaq100]
            self.repository.upsert_companies({
                "company_id": item.company_id, "country": "US", "company_name": item.name,
                "reporting_currency": "USD", "entity_kind": "general", "listed_from": None, "delisted_on": None,
            } for item in securities)
            self.repository.upsert_identifiers({
                "company_id": item.company_id, "identifier_type": "ticker", "identifier_value": item.ticker,
                "exchange": item.market_id, "valid_from": reference_date, "valid_to": None, "is_primary": True,
            } for item in securities)
            self.repository.upsert_identifiers({
                "company_id": item.company_id, "identifier_type": "cik", "identifier_value": item.cik,
                "exchange": None, "valid_from": reference_date, "valid_to": None, "is_primary": True,
            } for item in securities if item.cik)
            for market, rows in by_market.items():
                self.repository.save_us_universe(market, year, quarter, rows)
            self.repository.save_us_state("universe_backfill", "ready", {"period": f"{year}Q{quarter}"})
        return {
            "period": f"{year}Q{quarter}", "write": write, "status": "ready",
            "markets": {market: len(rows) for market, rows in by_market.items()},
            "requests": {"index_sources": self.constituents.request_count, "sec": self.sec.request_count},
        }

    def backfill_period(self, year: int, quarter: int, *, write: bool = True) -> dict:
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
                payload = self.sec.company_facts(member.cik)
                accessions = _all_financial_accessions(payload)
                changed.extend(
                    fact for fact in extract_new_sec_facts(member.company_id, payload, accessions)
                    if market_period(fact.period_end) == (year, quarter)
                )
            except ProviderError as exc:
                issues.append({"company": member.company_name, "reason": str(exc)})
        if write and changed:
            self.repository.replace_company_quarters_for_backfill(fact.db_row() for fact in changed)
            self.recalculate_market_period(year, quarter)
        status = "incomplete" if issues else "ready"
        result = {"period": f"{year}Q{quarter}", "write": write, "status": status,
                  "universe_companies": len(unique), "replaced_company_quarters": len(changed),
                  "issues": issues, "requests": {"sec": self.sec.request_count}}
        if write:
            self.repository.save_us_state("backfill", status, {"period": result["period"]})
        return result
