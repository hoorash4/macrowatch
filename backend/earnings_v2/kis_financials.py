from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import requests


@dataclass(frozen=True)
class KisTopLineResult:
    values: dict[str, Decimal]
    errors: dict[str, str]
    request_counts: dict[str, int] = field(default_factory=dict)


class KisTopLineClient:
    """Call the protected Supabase KIS adapter only for unresolved top lines."""

    def __init__(self, supabase_url: str, service_role_key: str, *, timeout: int = 120) -> None:
        self.url = f"{supabase_url.rstrip('/')}/functions/v1/earnings-kis-top-lines"
        self.key = service_role_key
        self.timeout = timeout
        self.session = requests.Session()

    def collect(self, tickers: Iterable[str], year: int, quarter: int) -> KisTopLineResult:
        unique = list(dict.fromkeys(str(ticker).strip() for ticker in tickers if str(ticker).strip()))
        if not unique:
            return KisTopLineResult(values={}, errors={}, request_counts={"edge_calls": 0, "tickers": 0})
        response = self.session.post(
            self.url,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
            json={"tickers": unique, "year": int(year), "quarter": int(quarter)},
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(f"KIS top-line adapter failed ({response.status_code}): {response.text[:300]}")
        payload = response.json()
        raw_values = payload.get("values") if isinstance(payload, dict) else {}
        raw_errors = payload.get("errors") if isinstance(payload, dict) else {}
        values: dict[str, Decimal] = {}
        for ticker, item in (raw_values.items() if isinstance(raw_values, dict) else []):
            raw = item.get("top_line") if isinstance(item, dict) else None
            try:
                values[str(ticker)] = Decimal(str(raw))
            except (InvalidOperation, TypeError, ValueError):
                continue
        errors = {
            str(ticker): str(message)[:240]
            for ticker, message in (raw_errors.items() if isinstance(raw_errors, dict) else [])
        }
        return KisTopLineResult(
            values=values,
            errors=errors,
            request_counts={"edge_calls": 1, "tickers": len(unique), "resolved": len(values)},
        )
