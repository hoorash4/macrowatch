from __future__ import annotations

from datetime import date, datetime, timezone
import os
import re

from .growth import calculate_company_growth
from .krx import KrxOpenApiClient
from .models import QuarterValue, UniverseCandidate
from .open_dart import OpenDartV2Client
from .pilot import build_recent_four_quarter_pilot
from .repository import EarningsV2Store
from .universe import MARKET_TARGETS, select_final_universe


QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _receipt_date(receipt: str) -> date:
    if re.fullmatch(r"\d{14}", receipt):
        return datetime.strptime(receipt[:8], "%Y%m%d").date()
    raise ValueError(f"Invalid DART receipt number: {receipt}")


def _store() -> EarningsV2Store:
    return EarningsV2Store(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"], timeout=120,
    )


def main() -> int:
    krx = KrxOpenApiClient.from_env()
    dart = OpenDartV2Client.from_env()
    store = _store()
    corp_map = dart.corp_code_map()
    plan = build_recent_four_quarter_pilot(
        end_year=2026, end_quarter=2, markets=("kr_largecap", "kr_kosdaq"),
    )
    quarter_rows_by_company: dict[str, list[QuarterValue]] = {}
    coverage: list[dict[str, object]] = []

    for market_id, year, quarter in plan.quarters:
        month, day = QUARTER_END[quarter]
        reference_date, securities = krx.last_trading_day(market_id, date(year, month, day))
        target = MARKET_TARGETS[market_id]
        selected_securities = [item for item in securities if item.stock_code in corp_map][:target]
        companies = []
        identifiers = []
        candidates = []
        complete_ids: list[str] = []
        missing: list[dict[str, str]] = []

        for security in selected_securities:
            corp_code, official_name = corp_map[security.stock_code]
            company_id = f"kr:{corp_code}"
            companies.append({
                "company_id": company_id, "country": "KR",
                "company_name": official_name or security.name,
                "reporting_currency": "KRW", "entity_kind": "general",
            })
            identifiers.extend([
                {"company_id": company_id, "identifier_type": "dart_corp_code", "identifier_value": corp_code, "is_primary": True},
                {"company_id": company_id, "identifier_type": "krx_code", "identifier_value": security.stock_code, "exchange": "KOSPI" if market_id == "kr_largecap" else "KOSDAQ", "is_primary": True},
            ])
            candidates.append(UniverseCandidate(
                company_id, official_name or security.name, "KRW", security.market_cap,
                reference_date,
            ))
            try:
                values = dart.quarter_values(corp_code, year, quarter)
            except Exception as error:
                missing.append({
                    "company_id": company_id,
                    "company_name": official_name or security.name,
                    "reason": str(error)[:180],
                })
                continue
            if values is None or values["currency"] not in ("KRW", ""):
                missing.append({
                    "company_id": company_id,
                    "company_name": official_name or security.name,
                    "reason": "filing unavailable or reporting currency is not KRW",
                })
                continue
            filing_id = str(values["source_filing_id"])
            row = QuarterValue(
                company_id=company_id, fiscal_year=year, fiscal_quarter=quarter,
                market_year=year, market_quarter=quarter,
                period_end=date(year, month, day), top_line=values["top_line"],
                operating_income=values["operating_income"], net_income=values["net_income"],
                currency="KRW", consolidation_scope=values["scope"],
                top_line_method=values["top_line_method"], source="open_dart",
                source_filing_id=filing_id, filing_date=_receipt_date(filing_id),
                quality_status="complete",
            )
            quarter_rows_by_company.setdefault(company_id, []).append(row)
            complete_ids.append(company_id)

        store.upsert_companies(companies)
        store.upsert_identifiers(identifiers)
        members = select_final_universe(
            market_id=market_id, market_year=year, market_quarter=quarter,
            candidates=candidates, selection_method="direct_market_cap", target_count=target,
        )
        store.replace_universe(market_id, year, quarter, members)
        coverage.append({
            "market_id": market_id, "year": year, "quarter": quarter,
            "reference_date": reference_date.isoformat(), "target": target,
            "universe": len(members), "financials": len(complete_ids), "missing": missing,
        })
        print(f"{market_id} {year}Q{quarter}: universe={len(members)}/{target}, financials={len(complete_ids)}/{target}")
        for issue in missing:
            print(
                f"  missing {issue['company_name']} ({issue['company_id']}): "
                f"{issue['reason']}"
            )

    for company_id, rows in quarter_rows_by_company.items():
        store.upsert_company_quarters(calculate_company_growth(rows))

    store.save_pipeline_state(
        source="korea_pilot", operation="2025q3_2026q2", cursor={"coverage": [
            {key: value for key, value in item.items() if key != "missing"}
            | {"missing_count": len(item["missing"])} for item in coverage
        ]},
        status="ready", last_success_at=datetime.now(timezone.utc),
    )
    print("KOREA_V2_PILOT_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
