from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

import requests


class EarningsV2StoreError(RuntimeError):
    pass


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class EarningsV2Store:
    """Service-role-only RPC client for the private earnings_v2 schema."""

    def __init__(self, url: str, service_role_key: str, *, timeout: int = 60) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key
        self.timeout = timeout
        self.session = requests.Session()

    def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        try:
            response = self.session.post(
                f"{self.url}/rest/v1/rpc/{name}",
                json=_json_value(payload),
                headers={
                    "apikey": self.service_role_key,
                    "Authorization": f"Bearer {self.service_role_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
        except Exception as error:
            raise EarningsV2StoreError(f"V2 RPC {name} failed: {error}") from None

    def upsert_companies(self, rows: Iterable[Any]) -> int:
        return int(self._rpc("earnings_v2_upsert_companies", {"p_rows": list(rows)}))

    def upsert_identifiers(self, rows: Iterable[Any]) -> int:
        return int(self._rpc("earnings_v2_upsert_identifiers", {"p_rows": list(rows)}))

    def upsert_company_quarters(self, rows: Iterable[Any]) -> int:
        return int(self._rpc("earnings_v2_upsert_company_quarters", {"p_rows": list(rows)}))

    def replace_universe(self, market_id: str, year: int, quarter: int, rows: Iterable[Any]) -> int:
        return int(self._rpc("earnings_v2_replace_universe", {
            "p_market_id": market_id,
            "p_market_year": year,
            "p_market_quarter": quarter,
            "p_rows": list(rows),
        }))

    def upsert_market_quarters(self, rows: Iterable[Any]) -> int:
        return int(self._rpc("earnings_v2_upsert_market_quarters", {"p_rows": list(rows)}))

    def get_company_quarters(self, company_id: str) -> list[dict[str, Any]]:
        result = self._rpc("earnings_v2_get_company_quarters", {"p_company_id": company_id})
        if not isinstance(result, list):
            raise EarningsV2StoreError("V2 company-quarter RPC returned a non-array result")
        return [row for row in result if isinstance(row, dict)]

    def get_market_inputs(self, market_id: str, year: int, quarter: int) -> list[dict[str, Any]]:
        result = self._rpc("earnings_v2_get_market_inputs", {
            "p_market_id": market_id,
            "p_market_year": year,
            "p_market_quarter": quarter,
        })
        if not isinstance(result, list):
            raise EarningsV2StoreError("V2 market-input RPC returned a non-array result")
        return [row for row in result if isinstance(row, dict)]

    def save_pipeline_state(
        self, *, source: str, operation: str, cursor: dict[str, Any], status: str,
        last_success_at: datetime | None = None, last_error: str | None = None,
    ) -> None:
        self._rpc("earnings_v2_save_pipeline_state", {
            "p_source": source,
            "p_operation": operation,
            "p_cursor": cursor,
            "p_status": status,
            "p_last_success_at": last_success_at,
            "p_last_error": last_error,
        })
