from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from .http import resilient_session, safe_request_failure


STORE_TIMEOUT = (5, 20)
TOKEN_TIMEOUT = (5, 10)
KIS_TOKEN_CACHE_KEY = "kis_access_token_prod"


class StoreError(RuntimeError):
    pass


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


class EarningsV2Repository:
    def __init__(self, url: str, service_key: str, *, session: Any | None = None) -> None:
        self.url = url.rstrip("/")
        self.key = service_key.strip()
        if not self.url or not self.key:
            raise ValueError("Supabase URL and service key are required")
        self.session = session or resilient_session()
        self.headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    @classmethod
    def from_env(cls) -> "EarningsV2Repository":
        return cls(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        try:
            response = self.session.post(
                f"{self.url}/rest/v1/rpc/{name}",
                headers=self.headers,
                json=_json(params),
                timeout=STORE_TIMEOUT,
            )
            response.raise_for_status()
            return response.json() if response.content else None
        except Exception as exc:
            raise StoreError(safe_request_failure("Supabase RPC", name, exc)) from None

    def upsert_companies(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_companies", {"p_rows": list(rows)}) or 0)

    def upsert_company_profiles(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_company_profiles", {"p_rows": list(rows)}) or 0)

    def upsert_identifiers(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_identifiers", {"p_rows": list(rows)}) or 0)

    def replace_universe(self, market_id: str, year: int, quarter: int, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_replace_universe", {"p_market_id": market_id, "p_market_year": year, "p_market_quarter": quarter, "p_rows": list(rows)}) or 0)

    def upsert_company_quarters(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_v6_upsert_company_quarters", {"p_rows": list(rows)}) or 0)

    def replace_company_quarters_for_backfill(self, rows: Iterable[dict[str, Any]]) -> int:
        """명시적 백필 범위만 원자적으로 삭제 후 공급자 원본으로 교체한다."""
        return int(self.rpc(
            "earnings_v2_v6_replace_company_quarters_for_backfill",
            {"p_rows": list(rows)},
        ) or 0)

    def upsert_market_quarters(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_v6_upsert_market_quarters", {"p_rows": list(rows)}) or 0)

    def company_history(self, company_ids: Iterable[str]) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_get_company_quarters_many", {"p_company_ids": list(dict.fromkeys(company_ids))})
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def company_periods(self, company_ids: Iterable[str], periods: Iterable[tuple[int, int]]) -> list[dict[str, Any]]:
        unique_periods = list(dict.fromkeys(periods))
        result = self.rpc("earnings_v2_get_company_quarters_for_periods", {
            "p_company_ids": list(dict.fromkeys(company_ids)),
            "p_periods": [
                {"fiscal_year": year, "fiscal_quarter": quarter}
                for year, quarter in unique_periods
            ],
        })
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def market_history(self, market_id: str) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_get_market_quarters", {"p_market_id": market_id})
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def market_periods(self, market_ids: Iterable[str], periods: Iterable[tuple[int, int]]) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_get_market_quarters_for_periods", {
            "p_market_ids": list(dict.fromkeys(market_ids)),
            "p_periods": [
                {"market_year": year, "market_quarter": quarter}
                for year, quarter in dict.fromkeys(periods)
            ],
        })
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def seasonal_windows(self, entity_type: str, entity_ids: Iterable[str]) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_get_seasonal_windows", {
            "p_entity_type": entity_type,
            "p_entity_ids": list(dict.fromkeys(entity_ids)),
        })
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def upsert_seasonal_windows(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_seasonal_windows", {"p_rows": list(rows)}) or 0)

    def universe(self, market_id: str, year: int, quarter: int) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_v6_get_universe", {
            "p_market_id": market_id,
            "p_market_year": year,
            "p_market_quarter": quarter,
        })
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def quarter_fx_rate(self, year: int, quarter: int, base_currency: str, quote_currency: str) -> dict[str, Any] | None:
        result = self.rpc("earnings_v2_get_quarter_fx_rate", {
            "p_fiscal_year": year,
            "p_fiscal_quarter": quarter,
            "p_base_currency": base_currency,
            "p_quote_currency": quote_currency,
        })
        if isinstance(result, list):
            return result[0] if result and isinstance(result[0], dict) else None
        return result if isinstance(result, dict) else None

    def upsert_quarter_fx_rate(self, row: dict[str, Any]) -> int:
        return int(self.rpc("earnings_v2_upsert_quarter_fx_rate", {"p_row": row}) or 0)

    def upsert_delisting_events(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc(
            "earnings_v2_upsert_delisting_events", {"p_rows": list(rows)},
        ) or 0)

    def delisting_events(
        self,
        corp_codes: Iterable[str],
        start: Any,
        end: Any,
    ) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_get_delisting_events", {
            "p_corp_codes": list(dict.fromkeys(corp_codes)),
            "p_start": start,
            "p_end": end,
        })
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def save_state(self, operation: str, status: str, cursor: dict[str, Any], error: str | None = None) -> None:
        self.rpc("earnings_v2_save_pipeline_state", {
            "p_source": "korea_v2", "p_operation": operation, "p_cursor": cursor,
            "p_status": status, "p_last_success_at": datetime.now(timezone.utc) if status in {"ready", "incomplete"} else None,
            "p_last_error": error,
        })

    def pipeline_state(self, operation: str) -> dict[str, Any] | None:
        result = self.rpc("earnings_v2_get_pipeline_state", {
            "p_source": "korea_v2", "p_operation": operation,
        })
        if isinstance(result, list):
            return result[0] if result and isinstance(result[0], dict) else None
        return result if isinstance(result, dict) else None

    def cached_kis_token(self) -> str | None:
        response = self.session.get(
            f"{self.url}/rest/v1/app_settings",
            headers=self.headers,
            params={"key": f"eq.{KIS_TOKEN_CACHE_KEY}", "select": "value", "limit": "1"},
            timeout=TOKEN_TIMEOUT,
        )
        if not response.ok:
            return None
        rows = response.json()
        value = rows[0].get("value", {}) if isinstance(rows, list) and rows else {}
        token = str(value.get("access_token") or "") if isinstance(value, dict) else ""
        expires = str(value.get("expires_at") or "") if isinstance(value, dict) else ""
        try:
            valid = datetime.fromisoformat(expires.replace("Z", "+00:00")) > datetime.now(timezone.utc) + timedelta(minutes=10)
        except ValueError:
            valid = False
        return token if token and valid else None

    def save_kis_token(self, token: str, expires_in: int) -> None:
        expires = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in, 60))
        response = self.session.post(
            f"{self.url}/rest/v1/app_settings",
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "key"},
            json={"key": KIS_TOKEN_CACHE_KEY, "value": {"access_token": token, "expires_at": expires.isoformat()}, "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": None},
            timeout=TOKEN_TIMEOUT,
        )
        if not response.ok:
            raise StoreError("Could not persist the shared KIS access token")

