from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

import requests


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
        self.session = session or requests.Session()
        self.headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    @classmethod
    def from_env(cls) -> "EarningsV2Repository":
        return cls(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        try:
            response = self.session.post(f"{self.url}/rest/v1/rpc/{name}", headers=self.headers, json=_json(params), timeout=90)
            response.raise_for_status()
            return response.json() if response.content else None
        except Exception:
            raise StoreError(f"Supabase RPC {name} failed") from None

    def upsert_companies(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_companies", {"p_rows": list(rows)}) or 0)

    def upsert_identifiers(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_identifiers", {"p_rows": list(rows)}) or 0)

    def replace_universe(self, market_id: str, year: int, quarter: int, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_replace_universe", {"p_market_id": market_id, "p_market_year": year, "p_market_quarter": quarter, "p_rows": list(rows)}) or 0)

    def upsert_company_quarters(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_company_quarters", {"p_rows": list(rows)}) or 0)

    def upsert_market_quarters(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_market_quarters", {"p_rows": list(rows)}) or 0)

    def company_history(self, company_ids: Iterable[str]) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_get_company_quarters_many", {"p_company_ids": list(dict.fromkeys(company_ids))})
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def market_history(self, market_id: str) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_get_market_quarters", {"p_market_id": market_id})
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []

    def save_state(self, operation: str, status: str, cursor: dict[str, Any], error: str | None = None) -> None:
        self.rpc("earnings_v2_save_pipeline_state", {
            "p_source": "korea_v2", "p_operation": operation, "p_cursor": cursor,
            "p_status": status, "p_last_success_at": datetime.now(timezone.utc) if status in {"ready", "incomplete"} else None,
            "p_last_error": error,
        })

    def cached_kis_token(self) -> str | None:
        response = self.session.get(
            f"{self.url}/rest/v1/app_settings",
            headers=self.headers,
            params={"key": "eq.kis_access_token_prod", "select": "value", "limit": "1"},
            timeout=30,
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
            json={"key": "kis_access_token_prod", "value": {"access_token": token, "expires_at": expires.isoformat()}, "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": None},
            timeout=30,
        )
        if not response.ok:
            raise StoreError("Could not persist the shared KIS access token")
