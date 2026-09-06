from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .constituents import _name_match_score
from .models import MarketSecurity, USFinancialFact
from .pipeline import (
    MARKETS, USEarningsAutomaticPipeline, fact_from_row, market_period,
    previous_market_period,
)
from .providers import ProviderError, normalize_cik
from .transform import extract_new_sec_facts
from .six_k import extract_q1_from_h1_six_k_fact


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


def _shift_fiscal_key(year: int, quarter: int, steps: int) -> tuple[int, int]:
    index = year * 4 + quarter - 1 + steps
    return index // 4, index % 4 + 1


def _noncolliding_backfill_fiscal_key(repository, fact: USFinancialFact, year: int, quarter: int) -> USFinancialFact:
    """Keep a 6-K calendar label from overwriting another physical fiscal quarter."""
    history_loader = getattr(repository, "company_history", None)
    if not callable(history_loader):
        return fact
    rows = history_loader([fact.company_id])
    target_index = year * 4 + quarter - 1
    parsed: list[tuple[int, int, int, int]] = []
    for row in rows:
        try:
            parsed.append((
                int(row["market_year"]), int(row["market_quarter"]),
                int(row["fiscal_year"]), int(row["fiscal_quarter"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    collision = any(
        (fiscal_year, fiscal_quarter) == fact.key
        and (market_year, market_quarter) != (year, quarter)
        for market_year, market_quarter, fiscal_year, fiscal_quarter in parsed
    )
    if not collision:
        return fact

    neighbours = sorted(
        parsed,
        key=lambda row: abs((row[0] * 4 + row[1] - 1) - target_index),
    )
    for market_year, market_quarter, fiscal_year, fiscal_quarter in neighbours:
        neighbour_index = market_year * 4 + market_quarter - 1
        if neighbour_index == target_index:
            continue
        candidate = _shift_fiscal_key(
            fiscal_year, fiscal_quarter, target_index - neighbour_index,
        )
        if not any(
            (existing_fy, existing_fq) == candidate
            and (existing_year, existing_quarter) != (year, quarter)
            for existing_year, existing_quarter, existing_fy, existing_fq in parsed
        ):
            return fact.with_changes(fiscal_year=candidate[0], fiscal_quarter=candidate[1])
    raise ValueError(
        f"No non-colliding fiscal key for {fact.company_id} {year}Q{quarter}"
    )


def _backfill_name_matches(left: str, right: str) -> bool:
    """Match legal issuer names after stripping historical feed share-class markers."""
    def clean(value: str) -> str:
        return " ".join(
            word for word in value.split()
            if word.lower().strip(".,()") not in {"cls", "cmn", "cs"}
        )

    return _name_match_score(clean(left), clean(right)) >= 100


def _historical_ticker_directory(rows: list[dict], directory: dict[str, str], issuer_rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """Keep historical ticker mappings unless a row carries today's issuer name on another CIK."""
    titles_by_cik: dict[str, list[str]] = {}
    for _, title, cik in issuer_rows:
        normalized = normalize_cik(cik)
        if normalized and title:
            titles_by_cik.setdefault(normalized, []).append(title)
    historical_ciks: dict[str, set[str]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        cik = normalize_cik(row.get("cik"))
        name = str(row.get("company_name") or "").strip()
        if not ticker or not cik:
            continue
        current_cik = normalize_cik(directory.get(ticker))
        if current_cik and current_cik != cik and any(
            _backfill_name_matches(name, title) for title in titles_by_cik.get(current_cik, ())
        ):
            continue
        historical_ciks.setdefault(ticker, set()).add(cik)
    return {
        ticker: next(iter(ciks)) for ticker, ciks in historical_ciks.items() if len(ciks) == 1
    }


def _verified_backfill_security(
    security: MarketSecurity,
    reference_date: date,
    directory: dict[str, str],
    issuer_titles: dict[str, list[str]],
    has_reference_facts,
) -> MarketSecurity:
    """Repair a resolver result only when the current named issuer has period coverage."""
    current_cik = normalize_cik(directory.get(security.ticker))
    if (
        current_cik and current_cik != normalize_cik(security.cik)
        and any(_backfill_name_matches(security.name, title) for title in issuer_titles.get(current_cik, ()))
        and has_reference_facts(current_cik, reference_date)
    ):
        return replace(security, cik=current_cik)
    return security


class USEarningsBackfillPipeline(USEarningsAutomaticPipeline):
    """Automatic collector's SEC interpretation, with authoritative period replacement."""

    six_k_backfill_mode = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._backfill_facts: dict[tuple[str, str], list] = {}
        self._backfill_payloads: dict[str, dict] = {}
        self._historical_companies: list[dict] | None = None
        self._current_sec_ciks: set[str] | None = None

    def freeze_universe_period(self, year: int, quarter: int, *, write: bool = True) -> dict:
        """Persist one exact historical index membership only after both 100-company sets validate."""
        reference_date = date(year, quarter * 3, 31 if quarter in {1, 4} else 30)
        directory = self.sec.ticker_directory()
        # Reuse only unambiguous mappings already validated and persisted by a
        # neighbouring historical period. Reused tickers with multiple CIKs
        # remain unresolved and must go through the period-scoped SEC search.
        # Also reject a polluted row that pairs today's issuer name and ticker
        # with a different CIK (for example TEAM/Atlassian on Target's CIK).
        historical_directory = _historical_ticker_directory(
            self.repository.us_active_companies(year), directory, self.sec.company_ticker_rows(),
        )
        issuer_titles: dict[str, list[str]] = {}
        for _, title, cik in self.sec.company_ticker_rows():
            normalized = normalize_cik(cik)
            if normalized and title:
                issuer_titles.setdefault(normalized, []).append(title)

        def verify(rows: list[MarketSecurity]) -> list[MarketSecurity]:
            verified = [
                _verified_backfill_security(
                    security, reference_date, directory, issuer_titles,
                    self.constituents._has_company_facts_for_reference,
                )
                for security in rows
            ]
            if len({security.company_id for security in verified}) != 100:
                raise ProviderError(f"{rows[0].market_id} identity verification did not preserve 100 companies")
            return verified

        sp100 = verify(self.constituents.sp100_historical(reference_date, directory, historical_directory))
        nasdaq100 = verify(self.constituents.nasdaq100(reference_date, directory, historical_directory))
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
        normalized_cik = cik.zfill(10)
        payload = self._backfill_payloads.get(normalized_cik)
        if payload is None:
            payload = self.sec.company_facts(cik)
            self._backfill_payloads[normalized_cik] = payload
        facts = extract_new_sec_facts(
            company_id, payload, _all_financial_accessions(payload), strict_annual_direct=True,
        )
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
            same_name = bool(name and _backfill_name_matches(member.company_name, name))
            if cik and cik != current_cik and (same_ticker or same_name):
                alternative_ciks.add(cik)
        result = []
        for cik in sorted(alternative_ciks):
            payload = self._backfill_payloads.get(cik)
            if payload is None:
                payload = self.sec.company_facts(cik)
                self._backfill_payloads[cik] = payload
            issuer_name = str(payload.get("entityName") or "").strip() if isinstance(payload, dict) else ""
            if issuer_name and not _backfill_name_matches(member.company_name, issuer_name):
                continue
            result.extend(
                fact for fact in self._company_facts_for_cik(member.company_id, cik)
                if market_period(fact.period_end) == (year, quarter)
            )
        return result

    def _q1_h1_bridge_candidate(self, member, year: int, quarter: int):
        """Use a later Q2/H1 6-K only to complete an incomplete calendar Q1."""
        if quarter != 1 or not hasattr(self.sec, "six_k_filings") or not hasattr(self.sec, "six_k_documents"):
            return None
        q2_end = date(year, 6, 30)
        filings = self.sec.six_k_filings(
            member.cik, filed_from=q2_end - timedelta(days=10), filed_to=q2_end + timedelta(days=120),
        )
        candidates = []
        for filing in filings:
            fact = extract_q1_from_h1_six_k_fact(
                member.company_id, filing, self.sec.six_k_documents(member.cik, filing), year, self._fx_to_usd,
            )
            if fact is not None:
                candidates.append(fact)
        return min(candidates, key=lambda fact: fact.filing_date) if candidates else None

    def _ifrs_annual_candidate(self, member, year: int, quarter: int):
        """Derive one fiscal Q4 from an IFRS 20-F/40-F and three stored exact quarters."""
        if not hasattr(self.sec, "company_facts"):
            return None
        payload = self._backfill_payloads.get(member.cik.zfill(10))
        if payload is None:
            payload = self.sec.company_facts(member.cik)
            self._backfill_payloads[member.cik.zfill(10)] = payload
        facts = payload.get("facts", {}).get("ifrs-full", {})
        if not isinstance(facts, dict):
            return None
        tags = {
            "top_line": ("Revenue", "RevenueFromContractsWithCustomers"),
            "operating_income": (
                "ProfitLossFromOperatingActivities", "OperatingProfitLossOperating",
                "ProfitLossBeforeFinancingAndIncomeTaxes",
            ),
            "net_income": ("ProfitLossAttributableToOwnersOfParent", "ProfitLoss"),
        }
        by_metric: dict[str, dict[str, tuple[Decimal, str, date, date, date]]] = {}
        for metric, metric_tags in tags.items():
            rows_by_accession: dict[str, tuple[Decimal, str, date, date, date]] = {}
            for tag in metric_tags:
                fact = facts.get(tag, {})
                units = fact.get("units", {}) if isinstance(fact, dict) else {}
                for currency, rows in units.items() if isinstance(units, dict) else ():
                    if currency not in {"USD", "EUR", "GBP", "CNY", "JPY"} or not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, dict) or str(row.get("form") or "").upper() not in {"20-F", "40-F"}:
                            continue
                        try:
                            start = date.fromisoformat(str(row["start"])); end = date.fromisoformat(str(row["end"]))
                            filed = date.fromisoformat(str(row["filed"])); value = Decimal(str(row["val"]))
                        except (KeyError, ValueError, InvalidOperation):
                            continue
                        accession = str(row.get("accn") or "")
                        if (
                            accession and market_period(end) == (year, quarter)
                            and (end - start).days + 1 >= 300
                        ):
                            rows_by_accession.setdefault(accession, (value, currency, start, end, filed))
            by_metric[metric] = rows_by_accession
        common = set.intersection(*(set(rows) for rows in by_metric.values())) if by_metric else set()
        if not common:
            return None
        accession = max(common, key=lambda item: max(by_metric[metric][item][4] for metric in tags))
        annual: dict[str, Decimal] = {}
        starts: list[date] = []; ends: list[date] = []; filed_dates: list[date] = []
        for metric in tags:
            value, currency, start, end, filed = by_metric[metric][accession]
            annual[metric] = value * self._fx_to_usd(currency, end)
            starts.append(start); ends.append(end); filed_dates.append(filed)
        history: dict[tuple[int, int], object] = {}
        for row in self.repository.company_history([member.company_id]):
            try:
                fact = fact_from_row(row)
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
            if fact.fully_complete:
                key = market_period(fact.period_end)
                current = history.get(key)
                if current is None or fact.filing_date > current.filing_date:
                    history[key] = fact
        prior_keys = []
        cursor = (year, quarter)
        for _ in range(3):
            cursor = previous_market_period(*cursor)
            prior_keys.append(cursor)
        if any(key not in history for key in prior_keys):
            return None
        return USFinancialFact(
            company_id=member.company_id,
            fiscal_year=max(ends).year,
            fiscal_quarter=4,
            period_start=min(starts),
            period_end=max(ends),
            top_line=annual["top_line"] - sum((history[key].top_line for key in prior_keys), Decimal(0)),
            operating_income=annual["operating_income"] - sum((history[key].operating_income for key in prior_keys), Decimal(0)),
            net_income=annual["net_income"] - sum((history[key].net_income for key in prior_keys), Decimal(0)),
            source_filing_id=accession,
            filing_date=max(filed_dates),
            is_pending=False,
        )

    def _ifrs_allocated_candidate(self, member, year: int, quarter: int):
        """Allocate IFRS half-year/year totals only when exact quarters do not exist.

        Foreign private issuers commonly publish H1 and FY statements instead of
        four standalone quarters. Splitting each disclosed half equally preserves
        the issuer's reported annual total while keeping this approximation inside
        the historical backfill path.
        """
        if not hasattr(self.sec, "company_facts"):
            return None
        payload = self._backfill_payloads.get(member.cik.zfill(10))
        if payload is None:
            payload = self.sec.company_facts(member.cik)
            self._backfill_payloads[member.cik.zfill(10)] = payload
        facts = payload.get("facts", {}).get("ifrs-full", {})
        if not isinstance(facts, dict):
            return None
        tags = {
            "top_line": ("Revenue", "RevenueFromContractsWithCustomers"),
            "operating_income": (
                "ProfitLossFromOperatingActivities", "OperatingProfitLossOperating",
                "ProfitLossBeforeFinancingAndIncomeTaxes",
            ),
            "net_income": ("ProfitLossAttributableToOwnersOfParent", "ProfitLoss"),
        }
        observations: dict[tuple[date, date, str, str], dict[str, tuple[Decimal, date]]] = {}
        for metric, metric_tags in tags.items():
            for tag in metric_tags:
                item = facts.get(tag, {})
                units = item.get("units", {}) if isinstance(item, dict) else {}
                for currency, rows in units.items() if isinstance(units, dict) else ():
                    if currency not in {"USD", "EUR", "GBP", "CNY", "JPY"} or not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, dict) or str(row.get("form") or "").upper() not in {"6-K", "20-F", "40-F"}:
                            continue
                        try:
                            start = date.fromisoformat(str(row["start"])); end = date.fromisoformat(str(row["end"]))
                            filed = date.fromisoformat(str(row["filed"])); value = Decimal(str(row["val"]))
                        except (KeyError, ValueError, InvalidOperation):
                            continue
                        duration = (end - start).days + 1
                        accession = str(row.get("accn") or "")
                        if accession and 150 <= duration <= 400:
                            observations.setdefault((start, end, accession, currency), {}).setdefault(
                                metric, (value, filed),
                            )
        complete = [
            (key, values) for key, values in observations.items()
            if set(values) == set(tags)
        ]
        target_index = year * 4 + quarter - 1

        def covered(start: date, end: date) -> bool:
            return start.year * 4 + (start.month - 1) // 3 <= target_index <= end.year * 4 + (end.month - 1) // 3

        halves = [(key, values) for key, values in complete if 150 <= (key[1] - key[0]).days + 1 <= 220]
        annuals = [(key, values) for key, values in complete if 300 <= (key[1] - key[0]).days + 1 <= 400]
        selected_values: dict[str, Decimal] | None = None
        source_ids: list[str] = []
        filing_dates: list[date] = []
        source_currency = "USD"
        relevant_half = max(
            ((key, values) for key, values in halves if covered(key[0], key[1])),
            key=lambda item: max(value[1] for value in item[1].values()), default=None,
        )
        if relevant_half is not None:
            key, values = relevant_half
            selected_values = {metric: value[0] / 2 for metric, value in values.items()}
            source_ids = [key[2]]; filing_dates = [value[1] for value in values.values()]
            source_currency = key[3]
        else:
            relevant_annual = max(
                ((key, values) for key, values in annuals if covered(key[0], key[1])),
                key=lambda item: max(value[1] for value in item[1].values()), default=None,
            )
            if relevant_annual is None:
                return None
            annual_key, annual_values = relevant_annual
            first_half = max(
                ((key, values) for key, values in halves
                 if key[0] == annual_key[0] and key[1] < annual_key[1] and key[3] == annual_key[3]),
                key=lambda item: item[0][1], default=None,
            )
            if first_half is not None and target_index > first_half[0][1].year * 4 + (first_half[0][1].month - 1) // 3:
                selected_values = {
                    metric: (annual_values[metric][0] - first_half[1][metric][0]) / 2
                    for metric in tags
                }
                source_ids = [annual_key[2], first_half[0][2]]
                filing_dates = [value[1] for value in annual_values.values()] + [
                    value[1] for value in first_half[1].values()
                ]
            else:
                selected_values = {metric: value[0] / 4 for metric, value in annual_values.items()}
                source_ids = [annual_key[2]]; filing_dates = [value[1] for value in annual_values.values()]
            source_currency = annual_key[3]
        end = date(year, quarter * 3, 31 if quarter in {1, 4} else 30)
        start = date(year, (quarter - 1) * 3 + 1, 1)
        rate = self._fx_to_usd(source_currency, end)
        return USFinancialFact(
            company_id=member.company_id, fiscal_year=year, fiscal_quarter=quarter,
            period_start=start, period_end=end,
            top_line=selected_values["top_line"] * rate,
            operating_income=selected_values["operating_income"] * rate,
            net_income=selected_values["net_income"] * rate,
            source_filing_id="allocated-ifrs:" + "+".join(dict.fromkeys(source_ids)),
            filing_date=max(filing_dates), is_pending=False,
        )

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
            if candidates and not any(fact.fully_complete for fact in candidates):
                try:
                    bridge = self._q1_h1_bridge_candidate(member, year, quarter)
                except ProviderError as exc:
                    if strict_provider_errors:
                        raise ProviderError(f"{member.company_name}: {exc}") from exc
                    bridge = None
                if bridge is not None:
                    direct = _select_backfill_fact(candidates)
                    candidates.append(direct.with_changes(
                        top_line=direct.top_line if direct.top_line is not None else bridge.top_line,
                        operating_income=direct.operating_income if direct.operating_income is not None else bridge.operating_income,
                        net_income=direct.net_income if direct.net_income is not None else bridge.net_income,
                        source_filing_id=f"{direct.source_filing_id}+{bridge.source_filing_id}",
                        filing_date=max(direct.filing_date, bridge.filing_date), is_pending=False,
                    ))
            if not candidates or not any(fact.fully_complete for fact in candidates):
                try:
                    annual_candidate = self._ifrs_annual_candidate(member, year, quarter)
                except ProviderError as exc:
                    if strict_provider_errors:
                        raise ProviderError(f"{member.company_name}: {exc}") from exc
                    annual_candidate = None
                if annual_candidate is not None:
                    candidates.append(annual_candidate)
            if not candidates or not any(fact.fully_complete for fact in candidates):
                try:
                    allocated_candidate = self._ifrs_allocated_candidate(member, year, quarter)
                except ProviderError as exc:
                    if strict_provider_errors:
                        raise ProviderError(f"{member.company_name}: {exc}") from exc
                    allocated_candidate = None
                if allocated_candidate is not None:
                    candidates.append(allocated_candidate)
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
            selected = _noncolliding_backfill_fiscal_key(
                self.repository, selected, year, quarter,
            )
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

