from __future__ import annotations

import os
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from earnings_v2.http import bounded_request, provider_session, safe_request_failure

from .models import MarketSecurity


KIS_BASE = "https://openapi.koreainvestment.com:9443"
SEC_DATA_BASE = "https://data.sec.gov"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
KIS_OVERSEAS_CAP_PATH = "/uapi/overseas-stock/v1/ranking/market-cap"
SEC_FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A"})


class ProviderError(RuntimeError):
    pass


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def normalize_cik(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(10) if digits else None


def ticker_candidates(ticker: str) -> tuple[str, ...]:
    value = ticker.strip().upper()
    return tuple(dict.fromkeys((value, value.replace("/", "-"), value.replace(".", "-"))))


class KisMarketCapClient:
    """KIS가 계산해 제공하는 당일 NYSE/NASDAQ 시총 순위만 읽는다."""

    def __init__(self, app_key: str, app_secret: str, *, session: Any | None = None) -> None:
        if not app_key.strip() or not app_secret.strip():
            raise ValueError("KIS credentials are required")
        self.app_key, self.app_secret = app_key.strip(), app_secret.strip()
        self.session = session or provider_session()
        self._token: str | None = None
        self.request_count = 0

    @classmethod
    def from_env(cls) -> "KisMarketCapClient":
        return cls(os.getenv("KIS_APP_KEY", ""), os.getenv("KIS_APP_SECRET", ""))

    def _token_value(self) -> str:
        if self._token:
            return self._token
        try:
            payload = bounded_request(
                self.session, "POST", f"{KIS_BASE}/oauth2/tokenP",
                provider="KIS", operation="access-token",
                json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
                total_timeout=30, attempt_timeout=10, connect_timeout=5, read_timeout=15,
            )
        except Exception as exc:
            raise ProviderError(safe_request_failure("KIS", "access-token", exc)) from None
        token = str(payload.get("access_token") or "") if isinstance(payload, dict) else ""
        if not token:
            raise ProviderError("KIS access-token response did not include a token")
        self._token = token
        return token

    def ranking(self, *, market_id: str, reference_date: date, ticker_to_cik: dict[str, str]) -> list[MarketSecurity]:
        exchange = {"us_nyse": "NYS", "us_nasdaq": "NAS"}[market_id]
        try:
            payload = bounded_request(
                self.session, "GET", f"{KIS_BASE}{KIS_OVERSEAS_CAP_PATH}",
                provider="KIS", operation=f"{exchange} market-cap ranking",
                params={"EXCD": exchange, "CURR_GB": "0", "VOL_RANG": "0", "KEYB": "", "AUTH": ""},
                headers={"authorization": f"Bearer {self._token_value()}", "appkey": self.app_key,
                         "appsecret": self.app_secret, "tr_id": "HHDFS76350100", "custtype": "P"},
                total_timeout=35, attempt_timeout=12, connect_timeout=5, read_timeout=20,
            )
        except Exception as exc:
            raise ProviderError(safe_request_failure("KIS", f"{exchange} market-cap ranking", exc)) from None
        self.request_count += 1
        if not isinstance(payload, dict) or str(payload.get("rt_cd") or "0") != "0":
            raise ProviderError(f"KIS {exchange} market-cap ranking returned an invalid response")
        rows: list[MarketSecurity] = []
        for raw in payload.get("output2", []):
            if not isinstance(raw, dict):
                continue
            ticker = str(raw.get("symb") or "").strip().upper()
            name = str(raw.get("name") or raw.get("ename") or "").strip()
            cap = _decimal(raw.get("tomv") or raw.get("mcap"))
            rank = int(str(raw.get("rank") or "0")) if str(raw.get("rank") or "").isdigit() else 0
            cik = next((ticker_to_cik[item] for item in ticker_candidates(ticker) if item in ticker_to_cik), None)
            if ticker and name and cap is not None and cap >= 0 and rank > 0:
                rows.append(MarketSecurity(ticker, name, cik, cap, rank, reference_date, market_id))
        rows.sort(key=lambda item: (item.rank, item.ticker))
        if len(rows) < 100:
            raise ProviderError(f"KIS {exchange} market-cap ranking returned {len(rows)}/100 rows")
        return rows[:100]


class SecEdgarClient:
    """SEC의 회사별 submissions/companyfacts 공식 JSON만 사용한다."""

    def __init__(self, user_agent: str, *, session: Any | None = None, interval: float = 0.12) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT must include an operator contact email")
        self.user_agent = user_agent
        self.session = session or provider_session()
        self.interval, self._last_request, self.request_count = interval, 0.0, 0

    @classmethod
    def from_env(cls) -> "SecEdgarClient":
        return cls(os.getenv("SEC_USER_AGENT", "").strip())

    def _get(self, url: str, operation: str) -> dict[str, Any]:
        remaining = self.interval - (time.monotonic() - self._last_request)
        if self._last_request and remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()
        try:
            payload = bounded_request(
                self.session, "GET", url, provider="SEC EDGAR", operation=operation,
                headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
                total_timeout=45, attempt_timeout=15, connect_timeout=5, read_timeout=25,
            )
        except Exception as exc:
            raise ProviderError(safe_request_failure("SEC EDGAR", operation, exc)) from None
        self.request_count += 1
        if not isinstance(payload, dict):
            raise ProviderError(f"SEC EDGAR {operation} returned invalid JSON")
        return payload

    def ticker_directory(self) -> dict[str, str]:
        payload = self._get(SEC_TICKERS_URL, "company tickers")
        result: dict[str, str] = {}
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            ticker, cik = str(row.get("ticker") or "").strip().upper(), normalize_cik(row.get("cik_str"))
            if ticker and cik:
                result[ticker] = cik
        return result

    def submissions(self, cik: str) -> dict[str, Any]:
        return self._get(f"{SEC_DATA_BASE}/submissions/CIK{normalize_cik(cik)}.json", f"submissions {cik}")

    def company_facts(self, cik: str) -> dict[str, Any]:
        return self._get(f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{normalize_cik(cik)}.json", f"company facts {cik}")

    def new_financial_accessions(self, cik: str, since: date) -> set[str]:
        recent = self.submissions(cik).get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            return set()
        forms, filed, accessions = recent.get("form", []), recent.get("filingDate", []), recent.get("accessionNumber", [])
        result: set[str] = set()
        for form, filed_on, accession in zip(forms, filed, accessions, strict=False):
            try:
                filed_date = date.fromisoformat(str(filed_on))
            except ValueError:
                continue
            if str(form).upper() in SEC_FORMS and filed_date > since:
                result.add(str(accession))
        return result
