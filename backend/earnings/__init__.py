"""Earnings Momentum source adapters and normalization helpers."""

from .corp_codes import DartCorporation, listed_corporations, parse_corp_code_archive
from .open_dart import OpenDartApiError, OpenDartBinaryResponse, OpenDartClient, OpenDartResponse
from .open_dart_parser import (
    DartAccountFact,
    parse_account_rows,
    select_preferred_accounts,
    standalone_quarter_value,
)
from .market_breadth import (
    MarketEarningsBreadthResult,
    MarketQuarter,
    OperatingIncomeObservation,
    calculate_market_earnings_breadth,
    calculate_market_earnings_history,
    observations_from_rows,
)
from .market_metrics import MarketAggregateMetric, calculate_market_metric_history

__all__ = [
    "DartAccountFact",
    "DartCorporation",
    "MarketEarningsBreadthResult",
    "MarketAggregateMetric",
    "MarketQuarter",
    "OpenDartApiError",
    "OpenDartBinaryResponse",
    "OpenDartClient",
    "OpenDartResponse",
    "OperatingIncomeObservation",
    "calculate_market_earnings_breadth",
    "calculate_market_earnings_history",
    "calculate_market_metric_history",
    "observations_from_rows",
    "parse_account_rows",
    "parse_corp_code_archive",
    "listed_corporations",
    "select_preferred_accounts",
    "standalone_quarter_value",
]
