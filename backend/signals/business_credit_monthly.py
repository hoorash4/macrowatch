"""Collect monthly business-credit series for the economic-chart dataset."""
from __future__ import annotations

from datetime import date

from sources.business_credit_monthly import (
    fetch_epiq_ch11_rows,
    fetch_equifax_rows,
    fetch_korea_business_delinquency_rows,
    fetch_korea_default_company_rows,
)
from signals.economic_chart_pipeline import _insert_missing


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _months_ago(d: date, months: int) -> date:
    serial = d.year * 12 + d.month - 1 - months
    return date(serial // 12, serial % 12 + 1, 1)


def collect_recent() -> dict[str, int]:
    """Collect enough recent months to tolerate delayed/revised monthly publication."""
    end = date.today()
    start = _months_ago(end, 18)
    counts: dict[str, int] = {}

    equifax = fetch_equifax_rows(start, end)
    for code, rows in equifax.items():
        counts[code] = _insert_missing(rows)

    epiq = fetch_epiq_ch11_rows(start, end, max_pages=4)
    counts["US_COMMERCIAL_CH11"] = _insert_missing(epiq)

    kr_delinquency = fetch_korea_business_delinquency_rows(start, end)
    counts["KR_CORP_DELINQ"] = _insert_missing(kr_delinquency)

    kr_default = fetch_korea_default_company_rows(start, end)
    counts["KR_DEFAULT_COMPANIES"] = _insert_missing(kr_default)
    return counts


if __name__ == "__main__":
    print({"inserted": collect_recent()})
