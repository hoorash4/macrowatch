"""Pure universe-diff and backfill planning for earnings collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Iterable, Mapping


DART_REPORT_SEQUENCE = ("11013", "11012", "11014", "11011")


@dataclass(frozen=True)
class Constituent:
    ticker: str
    company_name: str

    def __post_init__(self) -> None:
        ticker = self.ticker.strip()
        name = self.company_name.strip()
        if not re.fullmatch(r"\d{6}", ticker):
            raise ValueError(f"Invalid Korean stock ticker: {self.ticker!r}")
        if not name:
            raise ValueError("Constituent company_name is required.")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "company_name", name)


@dataclass(frozen=True)
class CompanyState:
    company_id: str
    ticker: str
    has_membership_history: bool
    has_any_active_membership: bool
    last_complete_period: tuple[int, str] | None = None


@dataclass(frozen=True)
class MembershipState:
    company_id: str
    ticker: str
    effective_from: date


@dataclass(frozen=True)
class UniverseAddition:
    constituent: Constituent
    company_id: str | None
    kind: str


@dataclass(frozen=True)
class UniverseExit:
    company_id: str
    ticker: str
    effective_to: date


@dataclass(frozen=True)
class UniversePlan:
    additions: tuple[UniverseAddition, ...]
    exits: tuple[UniverseExit, ...]
    unchanged_tickers: tuple[str, ...]


def plan_universe_sync(
    snapshot: Iterable[Constituent],
    *,
    companies_by_ticker: Mapping[str, CompanyState],
    current_memberships: Iterable[MembershipState],
    effective_from: date,
    expected_count: int | None = None,
) -> UniversePlan:
    """Classify a complete point-in-time constituent snapshot.

    ``new_company`` means MacroWatch has never tracked the company.
    ``reentry`` means it has membership history but collection stopped after all
    tracked memberships closed. ``cross_index_addition`` means another active
    universe kept the financial history continuous.
    """
    snapshot_by_ticker: dict[str, Constituent] = {}
    for constituent in snapshot:
        if constituent.ticker in snapshot_by_ticker:
            raise ValueError(f"Duplicate constituent ticker: {constituent.ticker}")
        snapshot_by_ticker[constituent.ticker] = constituent
    if expected_count is not None and len(snapshot_by_ticker) != expected_count:
        raise ValueError(
            f"Constituent snapshot count {len(snapshot_by_ticker)} does not match expected {expected_count}."
        )

    current_by_ticker = {membership.ticker: membership for membership in current_memberships}
    additions: list[UniverseAddition] = []
    unchanged: list[str] = []
    for ticker, constituent in sorted(snapshot_by_ticker.items()):
        if ticker in current_by_ticker:
            unchanged.append(ticker)
            continue
        company = companies_by_ticker.get(ticker)
        if company is None:
            additions.append(UniverseAddition(constituent, None, "new_company"))
        elif company.has_any_active_membership:
            additions.append(UniverseAddition(constituent, company.company_id, "cross_index_addition"))
        elif company.has_membership_history:
            additions.append(UniverseAddition(constituent, company.company_id, "reentry"))
        else:
            additions.append(UniverseAddition(constituent, company.company_id, "new_company"))

    exit_date = effective_from - timedelta(days=1)
    exits = [
        UniverseExit(membership.company_id, ticker, exit_date)
        for ticker, membership in sorted(current_by_ticker.items())
        if ticker not in snapshot_by_ticker
    ]
    return UniversePlan(tuple(additions), tuple(exits), tuple(unchanged))


def _period_index(period: tuple[int, str]) -> int:
    year, report_code = period
    try:
        quarter_index = DART_REPORT_SEQUENCE.index(report_code)
    except ValueError as error:
        raise ValueError(f"Unsupported OpenDART report code: {report_code}") from error
    return year * 4 + quarter_index


def backfill_periods(
    *,
    as_of_year: int,
    kind: str,
    new_company_years: int = 5,
    last_complete_period: tuple[int, str] | None = None,
) -> list[tuple[int, str]]:
    """Plan candidate DART periods; no-data responses are harmless and resumable.

    New companies receive a fixed trailing window. Reentries receive every
    period after the last complete one, regardless of how long the gap is.
    """
    if kind not in {"new_company", "reentry", "cross_index_addition"}:
        raise ValueError(f"Unsupported universe addition kind: {kind}")
    if kind == "cross_index_addition":
        return []
    if kind == "new_company":
        start_year = as_of_year - new_company_years + 1
        start_index = start_year * 4
    else:
        if last_complete_period is None:
            start_index = (as_of_year - new_company_years + 1) * 4
        else:
            start_index = _period_index(last_complete_period) + 1
    end_index = as_of_year * 4 + 3
    periods: list[tuple[int, str]] = []
    for value in range(start_index, end_index + 1):
        year, quarter_index = divmod(value, 4)
        periods.append((year, DART_REPORT_SEQUENCE[quarter_index]))
    return periods
