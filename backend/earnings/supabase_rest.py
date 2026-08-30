"""Minimal service-role REST client for the earnings workers.

The client deliberately keeps credentials in headers only.  Error messages
contain the Supabase endpoint and status, but never echo authorization headers
or request environment variables.
"""

from __future__ import annotations

import os
import re
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
            diagnostic = ""
            try:
                payload = error.response.json()
                if isinstance(payload, dict):
                    code = str(payload.get("code") or "unknown")[:80]
                    # Keep only a SQL identifier, never free-form provider text:
                    # PostgREST details can echo rejected values supplied by a
                    # caller and therefore must not be written to public logs.
                    raw_message = str(payload.get("message") or "")
                    match = re.search(r'constraint\s+["\']?([A-Za-z0-9_]+)', raw_message)
                    diagnostic = f"constraint {match.group(1)}" if match else ""
            except Exception:
                pass
            raise EarningsStoreError(
                f"Earnings RPC failed: {name} (HTTP {status}, code {code}"
                + (f", {diagnostic}" if diagnostic else "") + ")."
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
                    "ticker": "not.is.null",
                    # Current index membership, rather than the durable company
                    # master flag, defines the collection universe. Companies
                    # that leave every tracked ranking keep their history but
                    # must not remain in identifier sync or regular collection.
                    "earnings_index_memberships.effective_to": "is.null",
                    "select": (
                        "id,ticker,company_name,"
                        "earnings_index_memberships!inner(index_id)"
                    ),
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

    def enqueue_open_dart_backfill(self, *, as_of_year: int, years: int = 10) -> int:
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

    def list_current_sec_companies(self) -> list[dict[str, Any]]:
        result = self._rpc("list_current_sec_earnings_companies", {})
        if not isinstance(result, list):
            raise EarningsStoreError("SEC company list returned an invalid result.")
        return [row for row in result if isinstance(row, dict)]

    def get_current_collection_coverage(self, *, country: str) -> dict[str, Any]:
        result = self._rpc("get_current_earnings_collection_coverage", {
            "p_country": country,
        })
        if not isinstance(result, dict):
            raise EarningsStoreError("Earnings collection coverage returned an invalid result.")
        return result

    def upsert_sec_company_quarters(
        self,
        *,
        company_id: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        result = self._rpc("upsert_sec_company_quarters", {
            "p_company_id": company_id,
            "p_rows": rows,
        })
        if not isinstance(result, dict):
            raise EarningsStoreError("SEC quarter upsert returned an invalid result.")
        return result

    def list_open_dart_identity_gaps(self) -> list[dict[str, Any]]:
        result = self._rpc("list_earnings_open_dart_identity_gaps", {})
        if not isinstance(result, list):
            raise EarningsStoreError("OpenDART filing-identity gap response is not an array.")
        return [row for row in result if isinstance(row, dict)]

    def attach_open_dart_backfill_filings(self, filings: list[dict[str, Any]]) -> dict[str, Any]:
        result = self._rpc(
            "attach_earnings_open_dart_backfill_filings",
            {"p_filings": filings},
        )
        if not isinstance(result, dict):
            raise EarningsStoreError("OpenDART backfill filing attachment returned an invalid result.")
        return result

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

    def claim_open_dart_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        result = self._rpc("claim_earnings_open_dart_jobs", {"p_limit": limit})
        if not isinstance(result, list):
            raise EarningsStoreError("OpenDART job claim returned an invalid result.")
        return [row for row in result if isinstance(row, dict)]

    def complete_open_dart_job(
        self,
        *,
        job_id: int,
        filing: dict[str, Any],
        quarter: dict[str, Any] | None,
        outcome: str,
    ) -> dict[str, Any]:
        result = self._rpc("complete_earnings_open_dart_job", {
            "p_job_id": job_id,
            "p_filing": filing,
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

    def list_all_quarterly_financials(self, *, page_size: int = 1000) -> list[dict[str, Any]]:
        """Read the compact canonical quarters without fetching filing payloads."""
        endpoint = f"{self.url}/rest/v1/earnings_quarterly_financials"
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            try:
                response = self.session.get(
                    endpoint,
                    params={
                        "select": (
                            "company_id,fiscal_year,fiscal_quarter,period_end,revenue,"
                            "operating_income,net_income,currency,"
                            "consolidation_scope,canonical_version"
                        ),
                        "order": "company_id.asc,fiscal_year.asc,fiscal_quarter.asc",
                        "limit": str(page_size),
                        "offset": str(offset),
                    },
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                raise EarningsStoreError("Failed to list canonical quarterly financials.") from None
            if not isinstance(payload, list):
                raise EarningsStoreError("Canonical quarterly financial response is not an array.")
            page = [row for row in payload if isinstance(row, dict)]
            rows.extend(page)
            if len(payload) < page_size:
                return rows
            offset += page_size

    def list_current_price_companies(self) -> list[dict[str, Any]]:
        result = self._rpc("list_current_earnings_price_companies", {})
        if not isinstance(result, list):
            raise EarningsStoreError("Earnings price company list returned an invalid result.")
        return [row for row in result if isinstance(row, dict)]

    def list_all_quarterly_prices(self, *, page_size: int = 1000) -> list[dict[str, Any]]:
        endpoint = f"{self.url}/rest/v1/earnings_company_quarterly_prices"
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            try:
                response = self.session.get(
                    endpoint,
                    params={
                        "select": (
                            "company_id,market_year,market_quarter,price_date,"
                            "adjusted_close,currency,source"
                        ),
                        "order": "company_id.asc,market_year.asc,market_quarter.asc",
                        "limit": str(page_size),
                        "offset": str(offset),
                    },
                    headers=self._headers(), timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                raise EarningsStoreError("Failed to list company quarterly prices.") from None
            if not isinstance(payload, list):
                raise EarningsStoreError("Company quarterly price response is not an array.")
            rows.extend(row for row in payload if isinstance(row, dict))
            if len(payload) < page_size:
                return rows
            offset += page_size

    def _upsert_rows(
        self, table: str, rows: list[dict[str, Any]], conflict: str, *, batch_size: int = 500
    ) -> int:
        endpoint = f"{self.url}/rest/v1/{table}"
        stored = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            try:
                response = self.session.post(
                    endpoint, params={"on_conflict": conflict}, json=batch,
                    headers=self._headers(prefer="resolution=merge-duplicates,return=minimal"),
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except Exception:
                raise EarningsStoreError(f"Failed to store {table} rows.") from None
            stored += len(batch)
        return stored

    def upsert_quarterly_prices(self, rows: list[dict[str, Any]]) -> int:
        return self._upsert_rows(
            "earnings_company_quarterly_prices", rows,
            "company_id,market_year,market_quarter",
        )

    def upsert_company_price_gaps(self, rows: list[dict[str, Any]]) -> int:
        return self._upsert_rows(
            "earnings_company_price_gaps", rows,
            "company_id,market_year,market_quarter",
        )

    def get_app_setting(self, key: str) -> dict[str, Any] | None:
        endpoint = f"{self.url}/rest/v1/app_settings"
        try:
            response = self.session.get(
                endpoint,
                params={"key": f"eq.{key}", "select": "value", "limit": "1"},
                headers=self._headers(), timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise EarningsStoreError("Failed to read app setting.") from None
        if not isinstance(payload, list) or not payload:
            return None
        value = payload[0].get("value") if isinstance(payload[0], dict) else None
        return value if isinstance(value, dict) else None

    def set_app_setting(self, key: str, value: dict[str, Any]) -> None:
        endpoint = f"{self.url}/rest/v1/app_settings"
        try:
            response = self.session.post(
                endpoint, params={"on_conflict": "key"},
                json={"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()},
                headers=self._headers(prefer="resolution=merge-duplicates,return=minimal"),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            raise EarningsStoreError("Failed to save app setting.") from None

    def upsert_growth_metrics(
        self,
        rows: list[dict[str, Any]],
        *,
        batch_size: int = 500,
    ) -> int:
        """Persist compact recalculable metrics in bounded PostgREST batches."""
        endpoint = f"{self.url}/rest/v1/earnings_quarterly_growth_metrics"
        stored = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            try:
                response = self.session.post(
                    endpoint,
                    params={"on_conflict": "company_id,fiscal_year,fiscal_quarter"},
                    json=batch,
                    headers=self._headers(prefer="resolution=merge-duplicates,return=minimal"),
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except Exception:
                raise EarningsStoreError("Failed to store quarterly growth metrics.") from None
            stored += len(batch)
        return stored

    def list_growth_metric_versions(self, *, page_size: int = 1000) -> dict[tuple[str, int, int], tuple[int, int]]:
        """Return only version fingerprints needed to avoid unchanged writes."""
        endpoint = f"{self.url}/rest/v1/earnings_quarterly_growth_metrics"
        versions: dict[tuple[str, int, int], tuple[int, int]] = {}
        offset = 0
        while True:
            try:
                response = self.session.get(
                    endpoint,
                    params={
                        "select": (
                            "company_id,fiscal_year,fiscal_quarter,"
                            "source_canonical_version,calculation_version"
                        ),
                        "order": "company_id.asc,fiscal_year.asc,fiscal_quarter.asc",
                        "limit": str(page_size),
                        "offset": str(offset),
                    },
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                raise EarningsStoreError("Failed to list growth metric versions.") from None
            if not isinstance(payload, list):
                raise EarningsStoreError("Growth metric version response is not an array.")
            for row in payload:
                if not isinstance(row, dict):
                    continue
                key = (
                    str(row["company_id"]),
                    int(row["fiscal_year"]),
                    int(row["fiscal_quarter"]),
                )
                versions[key] = (
                    int(row["source_canonical_version"]),
                    int(row["calculation_version"]),
                )
            if len(payload) < page_size:
                return versions
            offset += page_size

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
