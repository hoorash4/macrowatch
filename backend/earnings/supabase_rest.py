"""Minimal service-role REST client for the earnings workers.

The client deliberately keeps credentials in headers only.  Error messages
contain the Supabase endpoint and status, but never echo authorization headers
or request environment variables.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

import requests


class EarningsStoreError(RuntimeError):
    """Supabase rejected an earnings worker request."""


class SupabaseEarningsStore:
    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        session: Any | None = None,
        timeout: int = 30,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key.strip()
        if not self.url or not self.service_role_key:
            raise ValueError("Supabase URL and service-role key are required.")
        self.timeout = timeout
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls, **kwargs: Any) -> "SupabaseEarningsStore":
        url = os.getenv("SUPABASE_URL", "").strip()
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError("Missing required Supabase worker environment variables.")
        return cls(url, key, **kwargs)

    def _headers(self, *, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def list_active_korean_companies(self) -> list[dict[str, Any]]:
        endpoint = f"{self.url}/rest/v1/earnings_companies"
        try:
            response = self.session.get(
                endpoint,
                params={
                    "country": "eq.KR",
                    "is_active": "eq.true",
                    "ticker": "not.is.null",
                    "select": "id,ticker,company_name",
                    "order": "ticker.asc",
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise EarningsStoreError("Failed to list active Korean earnings companies.") from None
        if not isinstance(payload, list):
            raise EarningsStoreError("Korean earnings company response is not an array.")
        return [row for row in payload if isinstance(row, dict)]

    def upsert_identifiers(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = list(rows)
        if not payload:
            return 0
        endpoint = f"{self.url}/rest/v1/earnings_company_identifiers"
        try:
            response = self.session.post(
                endpoint,
                params={"on_conflict": "company_id,identifier_type,identifier_value"},
                json=payload,
                headers=self._headers(prefer="resolution=merge-duplicates,return=minimal"),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            raise EarningsStoreError("Failed to save OpenDART company identifiers.") from None
        return len(payload)
