"""Collect monthly business-credit series for the economic-chart dataset."""
from __future__ import annotations

from datetime import date
import json

from common import SupabaseRest
from sources.business_credit_monthly import (
    fetch_korea_business_delinquency_rows,
    fetch_korea_default_company_rows,
)
from sources.court_rehabilitation import fetch_korea_corporate_rehab_rows
from sources.epiq_ch11_source import fetch_epiq_ch11_rows
from sources.paynet_loan_performance import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    fetch_paynet_rows,
)
from signals.economic_chart_pipeline import _insert_missing


def _months_ago(d: date, months: int) -> date:
    serial = d.year * 12 + d.month - 1 - months
    return date(serial // 12, serial % 12 + 1, 1)


def collect_recent() -> dict[str, int]:
    """Collect enough recent months to tolerate delayed monthly publication."""
    end = date.today()
    start = _months_ago(end, 18)
    db = SupabaseRest()
    counts: dict[str, int] = {}

    # Direct PayNet national series only. 31-180 is read as its own published series;
    # split 31-90 / 91-180 buckets are neither stored nor combined.
    paynet = fetch_paynet_rows(start, end)
    for code in (SERIES_DELINQUENCY, SERIES_DEFAULT):
        counts[code] = _insert_missing(db, paynet[code], start)

    epiq = fetch_epiq_ch11_rows(start, end, max_pages=4)
    counts["US_COMMERCIAL_CH11"] = _insert_missing(db, epiq, start)

    kr_delinquency = fetch_korea_business_delinquency_rows(start, end)
    counts["KR_CORP_DELINQ"] = _insert_missing(db, kr_delinquency, start)

    kr_default = fetch_korea_default_company_rows(start, end)
    counts["KR_DEFAULT_COMPANIES"] = _insert_missing(db, kr_default, start)

    court_start = _months_ago(end, 3)
    kr_rehab = fetch_korea_corporate_rehab_rows(court_start, end)
    counts["KR_CORP_REHAB"] = _insert_missing(db, kr_rehab, court_start)

    print(json.dumps({"mode": "automatic", "stage": "business-credit", "inserted": counts}, ensure_ascii=False, sort_keys=True))
    return counts


if __name__ == "__main__":
    collect_recent()
