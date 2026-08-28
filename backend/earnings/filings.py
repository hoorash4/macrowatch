"""Pure OpenDART periodic-filing interpretation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import re
from typing import Any, Iterable


REPORT_CODE_BY_MONTH = {
    "03": "11013",
    "06": "11012",
    "09": "11014",
    "12": "11011",
}


@dataclass(frozen=True)
class PeriodicFiling:
    corp_code: str
    receipt_no: str
    business_year: int
    report_code: str
    filed_on: str
    report_name: str
    is_correction: bool

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


def parse_periodic_filings(rows: Iterable[dict[str, Any]]) -> list[PeriodicFiling]:
    """Keep regular quarterly filings and their corrections without guessing."""
    filings: list[PeriodicFiling] = []
    seen_receipts: set[str] = set()
    for row in rows:
        corp_code = str(row.get("corp_code") or "").strip()
        receipt_no = str(row.get("rcept_no") or "").strip()
        filed_on = str(row.get("rcept_dt") or "").strip()
        report_name = str(row.get("report_nm") or "").strip()
        period = re.search(r"\((\d{4})\.(03|06|09|12)\)", report_name)
        if (
            not re.fullmatch(r"\d{8}", corp_code)
            or not re.fullmatch(r"\d{14}", receipt_no)
            or not re.fullmatch(r"\d{8}", filed_on)
            or not period
            or not any(label in report_name for label in ("분기보고서", "반기보고서", "사업보고서"))
            or receipt_no in seen_receipts
        ):
            continue
        year, month = period.groups()
        filings.append(PeriodicFiling(
            corp_code=corp_code,
            receipt_no=receipt_no,
            business_year=int(year),
            report_code=REPORT_CODE_BY_MONTH[month],
            filed_on=date(int(filed_on[:4]), int(filed_on[4:6]), int(filed_on[6:8])).isoformat(),
            report_name=report_name,
            is_correction="정정" in report_name,
        ))
        seen_receipts.add(receipt_no)
    return filings
