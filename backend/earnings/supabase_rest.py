"""Minimal service-role REST client for the earnings workers.

The client deliberately keeps credentials in headers only.  Error messages
contain the Supabase endpoint and status, but never echo authorization headers
or request environment variables.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

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
        timeout: int = 60,
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

    def _rpc(self, name: str, body: dict[str, Any]) -> Any:
        try:
            response = self.session.post(
                f"{self.url}/rest/v1/rpc/{name}",
                json=body,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as error:
            # Preserve only provider-safe diagnostics. Never echo request
            # headers, request bodies, URLs, or environment credentials.
            status = getattr(error.response, "status_code", "unknown")
            code = "unknown"
            try:
                payload = error.response.json()
                if isinstance(payload, dict):
                    code = str(payload.get("code") or "unknown")[:80]
            except Exception:
                pass
            raise EarningsStoreError(
                f"Earnings RPC failed: {name} (HTTP {status}, code {code})."
            ) from None
        except requests.RequestException as error:
            raise EarningsStoreError(
                f"Earnings RPC failed: {name} ({type(error).__name__})."
            ) from None
        except Exception:
            raise EarningsStoreError(f"Earnings RPC failed: {name} (invalid response).") from None

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

    def sync_open_dart_identifiers(
        self,
        rows: list[dict[str, Any]],
        *,
        valid_from: str,
    ) -> dict[str, Any]:
        result = self._rpc("sync_earnings_open_dart_identifiers", {
            "p_identifiers": rows,
            "p_valid_from": valid_from,
        })
        if not isinstance(result, dict):
            raise EarningsStoreError("OpenDART identifier sync returned an invalid result.")
        return result

    def enqueue_open_dart_backfill(self, *, as_of_year: int, years: int = 5) -> int:
        result = self._rpc("enqueue_earnings_open_dart_backfill", {
            "p_as_of_year": as_of_year,
            "p_years": years,
        })
        if not isinstance(result, int):
            raise EarningsStoreError("OpenDART backfill enqueue returned an invalid result.")
        return result

    def list_tracked_open_dart_codes(self) -> set[str]:
        endpoint = f"{self.url}/rest/v1/earnings_company_identifiers"
        try:
            response = self.session.get(
                endpoint,
                params={
                    "identifier_type": "eq.dart_corp_code",
                    "valid_to": "is.null",
                    "select": "identifier_value",
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise EarningsStoreError("Failed to list tracked OpenDART identifiers.") from None
        if not isinstance(payload, list):
            raise EarningsStoreError("Tracked OpenDART identifier response is not an array.")
        return {
            str(row.get("identifier_value") or "").strip()
            for row in payload if isinstance(row, dict)
            and str(row.get("identifier_value") or "").strip()
        }

    def get_checkpoint(self, *, source: str, operation: str) -> dict[str, Any] | None:
        endpoint = f"{self.url}/rest/v1/earnings_collection_checkpoints"
        try:
            response = self.session.get(
                endpoint,
                params={
                    "source": f"eq.{source}",
                    "operation": f"eq.{operation}",
                    "select": "cursor,last_success_at",
                    "limit": "1",
                },
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise EarningsStoreError("Failed to read earnings collection checkpoint.") from None
        if not isinstance(payload, list):
            raise EarningsStoreError("Earnings checkpoint response is not an array.")
        return payload[0] if payload and isinstance(payload[0], dict) else None

    def save_source_payload(
        self,
        *,
        operation: str,
        request_key: str,
        request_params: dict[str, Any],
        payload_sha256: str,
        payload: dict[str, Any],
    ) -> str:
        result = self._rpc("save_earnings_open_dart_payload", {
            "p_operation": operation,
            "p_request_key": request_key,
            "p_request_params": request_params,
            "p_payload_sha256": payload_sha256,
            "p_response_payload": payload,
        })
        if not isinstance(result, str) or not result:
            raise EarningsStoreError("OpenDART payload save returned an invalid identifier.")
        return result

    def claim_open_dart_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        result = self._rpc("claim_earnings_open_dart_jobs", {"p_limit": limit})
        if not isinstance(result, list):
            raise EarningsStoreError("OpenDART job claim returned an invalid result.")
        return [row for row in result if isinstance(row, dict)]

    def complete_open_dart_job(
        self,
        *,
        job_id: int,
        source_payload_id: str | None,
        filing: dict[str, Any],
        facts: list[dict[str, Any]],
        quarter: dict[str, Any] | None,
        outcome: str,
    ) -> dict[str, Any]:
        result = self._rpc("complete_earnings_open_dart_job", {
            "p_job_id": job_id,
            "p_source_payload_id": source_payload_id,
            "p_filing": filing,
            "p_facts": facts,
            "p_quarter": quarter,
            "p_outcome": outcome,
        })
        if not isinstance(result, dict):
            raise EarningsStoreError("OpenDART job completion returned an invalid result.")
        return result

    def fail_open_dart_job(
        self,
        *,
        job_id: int,
        error: str,
        retry_delay_seconds: int = 300,
    ) -> str | None:
        result = self._rpc("fail_earnings_open_dart_job", {
            "p_job_id": job_id,
            "p_error": error,
            "p_retry_delay_seconds": retry_delay_seconds,
        })
        return result if isinstance(result, str) else None

    def enqueue_open_dart_filings(self, filings: list[dict[str, Any]]) -> dict[str, Any]:
        result = self._rpc("enqueue_earnings_open_dart_filings", {"p_filings": filings})
        if not isinstance(result, dict):
            raise EarningsStoreError("OpenDART filing enqueue returned an invalid result.")
        return result

    def save_checkpoint(self, *, source: str, operation: str, cursor: dict[str, Any]) -> None:
        endpoint = f"{self.url}/rest/v1/earnings_collection_checkpoints"
        row = {
            "source": source,
            "operation": operation,
            "last_success_at": datetime.now(timezone.utc).isoformat(),
            "cursor": cursor,
            "consecutive_failures": 0,
            "last_error": None,
        }
        try:
            response = self.session.post(
                endpoint,
                params={"on_conflict": "source,operation"},
                json=row,
                headers=self._headers(prefer="resolution=merge-duplicates,return=minimal"),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            raise EarningsStoreError("Failed to save earnings collection checkpoint.") from None
