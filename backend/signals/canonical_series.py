"""Single storage boundary for reusable economic and market time series."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

from common import SupabaseRest


TABLE = "economic_chart_points"
UPSERT_BATCH_SIZE = 500
REQUIRED_FIELDS = frozenset({"series_code", "observation_date", "value", "frequency", "source"})

# Reusable series with more than one consumer have one runtime writer.  Feature
# pipelines may fetch the same source for an immediate calculation, but they
# must not publish another copy (or another sampling frequency) under the same
# canonical code. Market index prices are canonical in market_index_prices and
# therefore intentionally do not appear here.
SERIES_OWNERS = {
    "EM_OAS": "economic_chart",
    "WTI": "economic_chart",
    "WEI": "economic_chart",
    "EMRATIO": "economic_chart",
    "DRALACBS": "economic_chart",
    "US2Y": "economic_chart",
    "US10Y": "economic_chart",
    "US10Y_REAL": "economic_chart",
    "HY_OAS": "economic_chart",
    "NFCI_CREDIT": "economic_chart",
    "RRP": "economic_chart",
    "TGA": "economic_chart",
    "KR3Y": "economic_chart",
    "KR10Y": "economic_chart",
    "KR_POLICY_RATE": "economic_chart",
    "REDBOOK": "economic_chart",
    "US_RETAIL_SALES": "economic_chart",
    "KOSPI_PER": "economic_chart",
    "KOSPI_PBR": "economic_chart",
    "US_COMMERCIAL_CH11": "economic_chart",
    "KR_CORP_DELINQ": "economic_chart",
    "KR_DEFAULT_COMPANIES": "economic_chart",
    "KR_CORP_REHAB": "economic_chart",
    "US_SBDI_31_180": "economic_chart",
    "US_SBDFI": "economic_chart",
    "US_CPI": "inflation_rates",
    "US_CORE_CPI": "inflation_rates",
    "US_PPI": "inflation_rates",
    "US_CORE_PPI": "inflation_rates",
    "US_PCE": "inflation_rates",
    "US_CORE_PCE": "inflation_rates",
    "KR_CPI": "inflation_rates",
    "KR_CORE_CPI": "inflation_rates",
    "KR_PPI": "inflation_rates",
    "KR_IMPORT_PRICE": "inflation_rates",
    "USDKRW": "korea_foreign_flow",
    "KR_FOREIGN_NET_BUY": "korea_foreign_flow",
    "KOSPI_TRADING_VALUE": "korea_foreign_flow",
    "EM_DOLLAR_INDEX": "em_stress",
    "NFCI": "em_capital_capacity",
    "KR_KOFR": "liquidity",
    "OEF_PE": "equity_bond_attractiveness",
    "OEF_ADJUSTED_CLOSE": "equity_bond_attractiveness",
    "SPY_ADJUSTED_CLOSE": "equity_bond",
    "TLT_ADJUSTED_CLOSE": "equity_bond",
    "BAA10Y": "equity_bond",
    "US3M": "policy_expectation",
    "EFFR": "policy_expectation",
    "US_NFIB_SALES_EXPECTATION": "small_business_risk",
    "US_NFIB_BORROWING_DIFFICULTY": "small_business_risk",
    "US_NFIB_OPTIMISM": "small_business_risk",
    "KR_SME_FUNDING_OUTLOOK": "korea_small_business_risk",
    "KR_SME_UTILIZATION_SA": "korea_small_business_risk",
    "KR_SME_HEADLINE_OUTLOOK": "korea_small_business_risk",
    "KR_SME_LOAN_DELINQ": "korea_small_business_risk",
    "US_EBP": "financial_stress",
    "US_CMDI": "financial_stress",
    "NFCI_RISK": "financial_stress",
    "NFCI_NONFIN_LEVERAGE": "financial_stress",
    "US_COMMERCIAL_PAPER_3M": "financial_stress",
    "EM_HY_OAS": "em_stress",
    "EM_TAIL_RISK_OAS": "em_stress",
    "VXEEM": "em_stress",
    "EEM_CLOSE": "em_stress",
    "KR_BBB_YIELD": "korea_stress",
    "KR_AA_YIELD": "korea_stress",
    "KR_CP91": "korea_stress",
    "KR_CD91": "korea_stress",
    "KR_KORIBOR3M": "korea_stress",
    "BOK_FSI": "korea_stress",
    "US_SOFR": "liquidity",
    "US_IORB": "liquidity",
    "US_IOER": "liquidity",
    "US_FED_ASSETS": "liquidity",
    "KR_CALL_RATE": "liquidity",
    "KR_M2": "liquidity",
    "KR_LF": "liquidity",
    "KR_BOP_EQUITY_FLOW": "liquidity",
    "KR_BOP_BOND_FLOW": "liquidity",
}


def load(database: SupabaseRest, series_code: str, *, start: date | None = None,
         end: date | None = None) -> dict[date, float]:
    values: dict[date, float] = {}
    offset = 0
    while True:
        params = {"select": "observation_date,value", "series_code": f"eq.{series_code}",
                  "order": "observation_date.asc", "offset": str(offset), "limit": "1000"}
        if start is not None:
            params["observation_date"] = f"gte.{start.isoformat()}"
        if end is not None:
            params["and"] = f"(observation_date.lte.{end.isoformat()})"
        rows = database.request("GET", TABLE, params=params) or []
        for row in rows:
            if row.get("value") is not None:
                values[date.fromisoformat(str(row["observation_date"])[:10])] = float(row["value"])
        if len(rows) < 1000:
            return values
        offset += len(rows)


def load_many(database: SupabaseRest, codes: Iterable[str], *, start: date | None = None,
              end: date | None = None) -> dict[str, dict[date, float]]:
    return {code: load(database, code, start=start, end=end) for code in codes}


def rows(series_code: str, values: dict[date, float], *, frequency: str,
         source: str) -> list[dict[str, object]]:
    return [{"series_code": series_code, "observation_date": observed.isoformat(),
             "value": round(value, 8), "frequency": frequency, "source": source}
            for observed, value in sorted(values.items())]


def _validate_payload(payload: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    keys: set[tuple[str, str]] = set()
    for index, item in enumerate(payload):
        missing = REQUIRED_FIELDS.difference(item)
        if missing:
            raise ValueError(f"canonical series row {index} is missing: {', '.join(sorted(missing))}")
        series_code = str(item["series_code"] or "").strip()
        observation_date = str(item["observation_date"] or "")[:10]
        if not series_code or len(observation_date) != 10:
            raise ValueError(f"canonical series row {index} has an invalid key")
        key = (series_code, observation_date)
        if key in keys:
            raise ValueError(f"duplicate canonical series key in one write: {series_code}/{observation_date}")
        keys.add(key)
        normalized.append(dict(item))
    return normalized


def store(database: SupabaseRest, payload: Iterable[Mapping[str, object]], *, owner: str) -> int:
    """Validate and persist canonical observations through the sole Python write boundary."""
    rows_to_store = _validate_payload(payload)
    derived = sorted({str(row["series_code"]) for row in rows_to_store
                      if str(row.get("source") or "").startswith(("DERIVED:", "RESAMPLED:", "INTERNAL:"))})
    if derived:
        raise ValueError(f"calculated series must use derived_series.store: {', '.join(derived)}")
    conflicts = sorted({
        str(row["series_code"])
        for row in rows_to_store
        if (expected := SERIES_OWNERS.get(str(row["series_code"]))) is not None
        and expected != owner
    })
    if conflicts:
        raise ValueError(
            f"canonical series owner mismatch for {', '.join(conflicts)}: writer={owner}"
        )
    for offset in range(0, len(rows_to_store), UPSERT_BATCH_SIZE):
        database.upsert(TABLE, rows_to_store[offset:offset + UPSERT_BATCH_SIZE],
                        conflict="series_code,observation_date")
    return len(rows_to_store)
