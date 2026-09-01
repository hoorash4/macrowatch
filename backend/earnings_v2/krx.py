from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import os
import re
from typing import Any

import requests

from .http import get_with_retries, resilient_session


KRX_BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/sto"
MARKET_ENDPOINTS = {"kr_largecap": "stk_bydd_trd", "kr_kosdaq": "ksq_bydd_trd"}


@dataclass(frozen=True)
class KrxSecurity:
    stock_code: str
    name: str
    close: Decimal
    market_cap: Decimal
    listed_shares: Decimal
    reference_date: date


def _number(value: Any) -> Decimal:
    return Decimal(str(value or "0").replace(",", "").strip() or "0")


def _stock_code(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{6}", text):
        return text
    match = re.search(r"KR\d(\d{6})", text)
    return match.group(1) if match else ""


def is_eligible_common_stock(name: str, stock_code: str) -> bool:
    normalized = re.sub(r"\s+", "", name)
    if not re.fullmatch(r"\d{6}", stock_code):
        return False
    blocked = ("스팩", "리츠", "인프라", "부동산", "우선주")
    if any(token in normalized for token in blocked):
        return False
    # Korean preferred share names conventionally end in 우, 우B, 우C, etc.
    if re.search(r"우(?:[A-Z]|\d+[A-Z]?)?$", normalized):
        return False
    return True


class KrxOpenApiClient:
    def __init__(self, auth_key: str, *, session: requests.Session | None = None) -> None:
        if not auth_key.strip():
            raise ValueError("KRX OPEN API key is required")
        self.auth_key = auth_key.strip()
        self.session = session or resilient_session()

    @classmethod
    def from_env(cls) -> "KrxOpenApiClient":
        key = os.getenv("KRX_OPEN_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing KRX_OPEN_API_KEY")
        return cls(key)

    def daily_market(self, market_id: str, trading_date: date) -> list[KrxSecurity]:
        endpoint = MARKET_ENDPOINTS[market_id]
        response = get_with_retries(
            self.session,
            f"{KRX_BASE_URL}/{endpoint}",
            params={"basDd": trading_date.strftime("%Y%m%d")},
            headers={"AUTH_KEY": self.auth_key, "Accept": "application/json"},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("OutBlock_1") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"KRX {endpoint} returned an invalid response")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = _stock_code(row.get("ISU_CD"))
            name = str(row.get("ISU_NM") or "").strip()
            if not is_eligible_common_stock(name, code):
                continue
            result.append(KrxSecurity(
                stock_code=code,
                name=name,
                close=_number(row.get("TDD_CLSPRC")),
                market_cap=_number(row.get("MKTCAP")),
                listed_shares=_number(row.get("LIST_SHRS")),
                reference_date=trading_date,
            ))
        return sorted(result, key=lambda item: (-item.market_cap, item.stock_code))

    def last_trading_day(self, market_id: str, on_or_before: date) -> tuple[date, list[KrxSecurity]]:
        candidate = on_or_before
        for _ in range(12):
            rows = self.daily_market(market_id, candidate)
            if rows:
                return candidate, rows
            candidate -= timedelta(days=1)
        raise RuntimeError(f"No KRX trading day found on or before {on_or_before}")
