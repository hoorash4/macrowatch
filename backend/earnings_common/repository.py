from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from earnings_common.http import resilient_session, safe_request_failure


STORE_TIMEOUT = (5, 20)
_ALLOWED_WRITE_MODES = {"automatic", "manual", "repair", "backfill"}
_PERIOD_OPERATION = re.compile(r"^(\d{4})Q([1-4])$")


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


def _latest_completed_period(today: date | None = None) -> tuple[int, int]:
    current = today or date.today()
    ordinal = current.year * 4 + (current.month - 1) // 3 - 1
    return ordinal // 4, ordinal % 4 + 1


def _row_period(row: dict[str, Any]) -> tuple[int, int] | None:
    year = row.get("market_year")
    quarter = row.get("market_quarter")
    if year is None or quarter is None:
        year = row.get("fiscal_year")
        quarter = row.get("fiscal_quarter")
    try:
        return int(year), int(quarter)
    except (TypeError, ValueError):
        return None


class EarningsRepository:
    """Shared RPC contract with a hard automatic/manual write boundary.

    GitHub Actions default to restrictive ``automatic`` mode. Explicit repair
    and historical jobs must opt in with ``EARNINGS_WRITE_MODE=manual|repair|backfill``.
    Automatic mode is protected twice: the client drops historical fact/market
    rows before RPC calls, and dedicated DB RPCs reject any non-current quarter.
    """

    state_source = "korea_v2"
    failure_formatter = staticmethod(safe_request_failure)
    error_type = StoreError

    def __init__(self, url: str, service_key: str, *, session: Any | None = None) -> None:
        self.url = url.rstrip("/")
        self.key = service_key.strip()
        if not self.url or not self.key:
            raise ValueError("Supabase URL and service key are required")
        self.session = session or resilient_session()
        self.headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        configured_mode = os.getenv("EARNINGS_WRITE_MODE", "").strip().lower()
        default_mode = "automatic" if os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true" else "manual"
        self.write_mode = configured_mode or default_mode
        if self.write_mode not in _ALLOWED_WRITE_MODES:
            raise ValueError(f"Unsupported EARNINGS_WRITE_MODE: {self.write_mode}")

    @classmethod
    def from_env(cls) -> "EarningsRepository":
        return cls(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""))

    def _automatic_period(self) -> tuple[int, int]:
        return _latest_completed_period()

    def _state_source_name(self) -> str:
        return self.state_source if self.write_mode == "automatic" else f"{self.state_source}_{self.write_mode}"

    def _guard_automatic_period(self, year: int, quarter: int, operation: str) -> None:
        if self.write_mode != "automatic":
            return
        requested = (int(year), int(quarter))
        allowed = self._automatic_period()
        if requested != allowed:
            raise self.error_type(
                f"Automatic earnings write blocked for {requested[0]}Q{requested[1]} during {operation}; "
                f"only {allowed[0]}Q{allowed[1]} is writable",
            )

    def _filter_automatic_rows(self, rows: Iterable[dict[str, Any]], operation: str) -> list[dict[str, Any]]:
        materialized = list(rows)
        if self.write_mode != "automatic":
            return materialized
        allowed = self._automatic_period()
        accepted: list[dict[str, Any]] = []
        blocked: list[str] = []
        for row in materialized:
            period = _row_period(row)
            if period is None or period == allowed:
                accepted.append(row)
            else:
                blocked.append(f"{period[0]}Q{period[1]}")
        if blocked:
            print(json.dumps({
                "stage": "earnings_write_guard",
                "operation": operation,
                "write_mode": self.write_mode,
                "allowed_period": f"{allowed[0]}Q{allowed[1]}",
                "blocked_rows": len(blocked),
                "blocked_periods": sorted(set(blocked)),
            }, ensure_ascii=False), flush=True)
        return accepted

    def _filter_automatic_seasonal_windows(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        materialized = list(rows)
        if self.write_mode != "automatic":
            return materialized
        allowed = self._automatic_period()
        accepted: list[dict[str, Any]] = []
        blocked = 0
        for row in materialized:
            years = row.get("sample_years")
            quarter = row.get("fiscal_quarter")
            if not isinstance(years, (list, tuple)) or not years or quarter is None:
                accepted.append(row)
                continue
            try:
                period = (max(int(year) for year in years), int(quarter))
            except (TypeError, ValueError):
                accepted.append(row)
                continue
            if period == allowed:
                accepted.append(row)
            else:
                blocked += 1
        if blocked:
            print(json.dumps({
                "stage": "earnings_write_guard",
                "operation": "upsert_seasonal_windows",
                "write_mode": self.write_mode,
                "allowed_period": f"{allowed[0]}Q{allowed[1]}",
                "blocked_rows": blocked,
            }, ensure_ascii=False), flush=True)
        return accepted

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
            raise self.error_type(self.failure_formatter("Supabase RPC", name, exc)) from None

    def upsert_companies(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_companies", {"p_rows": list(rows)}) or 0)

    def upsert_company_profiles(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_company_profiles", {"p_rows": list(rows)}) or 0)

    def upsert_identifiers(self, rows: Iterable[dict[str, Any]]) -> int:
        return int(self.rpc("earnings_v2_upsert_identifiers", {"p_rows": list(rows)}) or 0)

    def replace_universe(self, market_id: str, year: int, quarter: int, rows: Iterable[dict[str, Any]]) -> int:
        self._guard_automatic_period(year, quarter, "replace_universe")
        rpc_name = (
            "earnings_v2_auto_replace_universe"
            if self.write_mode == "automatic" else "earnings_v2_replace_universe"
        )
        return int(self.rpc(rpc_name, {"p_market_id": market_id, "p_market_year": year, "p_market_quarter": quarter, "p_rows": list(rows)}) or 0)

    def upsert_company_quarters(self, rows: Iterable[dict[str, Any]]) -> int:
        accepted = self._filter_automatic_rows(rows, "upsert_company_quarters")
        if not accepted:
            return 0
        rpc_name = (
            "earnings_v2_auto_v6_upsert_company_quarters"
            if self.write_mode == "automatic" else "earnings_v2_v6_upsert_company_quarters"
        )
        return int(self.rpc(rpc_name, {"p_rows": accepted}) or 0)

    def upsert_market_quarters(self, rows: Iterable[dict[str, Any]]) -> int:
        accepted = self._filter_automatic_rows(rows, "upsert_market_quarters")
        if not accepted:
            return 0
        rpc_name = (
            "earnings_v2_auto_v6_upsert_market_quarters"
            if self.write_mode == "automatic" else "earnings_v2_v6_upsert_market_quarters"
        )
        return int(self.rpc(rpc_name, {"p_rows": accepted}) or 0)

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
        accepted = self._filter_automatic_seasonal_windows(rows)
        return int(self.rpc("earnings_v2_upsert_seasonal_windows", {"p_rows": accepted}) or 0) if accepted else 0

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
        period = _row_period(row)
        if period is not None:
            self._guard_automatic_period(*period, "upsert_quarter_fx_rate")
        rpc_name = (
            "earnings_v2_auto_upsert_quarter_fx_rate"
            if self.write_mode == "automatic" else "earnings_v2_upsert_quarter_fx_rate"
        )
        return int(self.rpc(rpc_name, {"p_row": row}) or 0)

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
        if self.write_mode == "automatic":
            match = _PERIOD_OPERATION.fullmatch(operation)
            if match is not None:
                self._guard_automatic_period(int(match.group(1)), int(match.group(2)), "save_state")
        self.rpc("earnings_v2_save_pipeline_state", {
            "p_source": self._state_source_name(), "p_operation": operation, "p_cursor": cursor,
            "p_status": status, "p_last_success_at": datetime.now(timezone.utc) if status in {"ready", "incomplete"} else None,
            "p_last_error": error,
        })

    def pipeline_state(self, operation: str) -> dict[str, Any] | None:
        result = self.rpc("earnings_v2_get_pipeline_state", {
            "p_source": self._state_source_name(), "p_operation": operation,
        })
        if isinstance(result, list):
            return result[0] if result and isinstance(result[0], dict) else None
        return result if isinstance(result, dict) else None

    def pending_rows(self) -> list[dict[str, Any]]:
        result = self.rpc("earnings_v2_list_pending", {})
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []
