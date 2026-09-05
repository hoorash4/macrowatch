from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from earnings_v2.repository import EarningsV2Repository

from .models import USCompany


class USEarningsRepository(EarningsV2Repository):
    SOURCE = "us_automatic"

    def us_universe(self, market_id: str, year: int, quarter: int) -> list[USCompany]:
        rows = self.rpc("earnings_v2_us_get_universe", {
            "p_market_id": market_id, "p_market_year": year, "p_market_quarter": quarter,
        }) or []
        return [USCompany(
            company_id=str(row["company_id"]), company_name=str(row["company_name"]),
            ticker=str(row.get("ticker") or ""), cik=str(row["cik"]) if row.get("cik") else None,
            market_id=str(row["market_id"]), rank=int(row["market_cap_rank"]),
            market_cap=row["market_cap"], reference_date=row["reference_date"],
        ) for row in rows]

    def us_active_companies(self, since_year: int) -> list[dict[str, Any]]:
        rows = self.rpc("earnings_v2_us_active_companies", {"p_since_year": since_year}) or []
        return [row for row in rows if isinstance(row, dict)]

    def us_market_facts(self, market_id: str, year: int, quarter: int) -> list[dict[str, Any]]:
        rows = self.rpc("earnings_v2_us_market_facts", {
            "p_market_id": market_id, "p_market_year": year, "p_market_quarter": quarter,
        }) or []
        return [row for row in rows if isinstance(row, dict)]

    def save_us_state(self, operation: str, status: str, cursor: dict[str, Any], error: str | None = None) -> None:
        self.rpc("earnings_v2_save_pipeline_state", {
            "p_source": self.SOURCE, "p_operation": operation, "p_cursor": cursor,
            "p_status": status,
            "p_last_success_at": datetime.now(timezone.utc) if status in {"ready", "incomplete"} else None,
            "p_last_error": error,
        })

    def us_state(self, operation: str) -> dict[str, Any] | None:
        result = self.rpc("earnings_v2_get_pipeline_state", {"p_source": self.SOURCE, "p_operation": operation})
        return result[0] if isinstance(result, list) and result and isinstance(result[0], dict) else None

    def save_us_universe(self, market_id: str, year: int, quarter: int, rows: Iterable[USCompany]) -> int:
        records = [{
            "market_id": item.market_id, "market_year": year, "market_quarter": quarter,
            "reference_date": item.reference_date, "company_id": item.company_id,
            "market_cap_rank": item.rank, "market_cap": item.market_cap,
            "currency": "USD", "selection_method": "direct_market_cap",
        } for item in rows]
        return self.replace_universe(market_id, year, quarter, records)
