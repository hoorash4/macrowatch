from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import os
from typing import Any

from .http import get_with_retries, resilient_session


ECOS_BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
USD_KRW_STAT_CODE = "731Y001"
USD_KRW_ITEM_CODE = "0000001"


class EcosFxError(RuntimeError):
    """Credential-safe ECOS exchange-rate failure."""


class EcosFxClient:
    """Fetch one closing USD/KRW rate per quarter and cache it in memory."""

    def __init__(self, api_key: str, *, session: Any | None = None) -> None:
        if not api_key.strip():
            raise ValueError("ECOS API key is required")
        self.api_key = api_key.strip()
        self.session = session if session is not None else resilient_session()
        self._cache: dict[date, Decimal] = {}

    @classmethod
    def from_env(cls) -> "EcosFxClient":
        key = os.getenv("ECOS_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing ECOS_API_KEY")
        return cls(key)

    def usd_krw_on_or_before(self, reference_date: date) -> Decimal:
        cached = self._cache.get(reference_date)
        if cached is not None:
            return cached
        start = reference_date - timedelta(days=10)
        url = (
            f"{ECOS_BASE_URL}/{self.api_key}/json/kr/1/100/"
            f"{USD_KRW_STAT_CODE}/D/{start:%Y%m%d}/{reference_date:%Y%m%d}/"
            f"{USD_KRW_ITEM_CODE}"
        )
        try:
            response = get_with_retries(
                self.session,
                url,
                timeout=(10, 30),
                attempts=3,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            # The ECOS credential is embedded in the URL, so transport errors
            # must never be forwarded to CI logs.
            raise EcosFxError("ECOS USD/KRW request failed") from None
        if not isinstance(payload, dict) or payload.get("RESULT"):
            raise EcosFxError("ECOS USD/KRW response is unavailable")
        search = payload.get("StatisticSearch")
        if not isinstance(search, dict) or search.get("RESULT"):
            raise EcosFxError("ECOS USD/KRW response is unavailable")
        rows = search.get("row", [])
        candidates: list[tuple[date, Decimal]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                observed = date.fromisoformat(
                    f"{str(row.get('TIME') or '')[:4]}-"
                    f"{str(row.get('TIME') or '')[4:6]}-"
                    f"{str(row.get('TIME') or '')[6:8]}"
                )
                rate = Decimal(str(row.get("DATA_VALUE") or "").replace(",", ""))
            except (ValueError, InvalidOperation):
                continue
            if observed <= reference_date and rate > 0:
                candidates.append((observed, rate))
        if not candidates:
            raise EcosFxError("ECOS returned no USD/KRW rate on or before quarter end")
        rate = max(candidates, key=lambda item: item[0])[1]
        self._cache[reference_date] = rate
        return rate
