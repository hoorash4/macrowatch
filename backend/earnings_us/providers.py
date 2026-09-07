from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from earnings_v2.http import bounded_request, provider_session, safe_request_failure

from .models import MarketSecurity
from .six_k import SixKDocument, SixKFiling, linked_financial_documents


KIS_BASE = "https://openapi.koreainvestment.com:9443"
SEC_DATA_BASE = "https://data.sec.gov"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
KIS_OVERSEAS_CAP_PATH = "/uapi/overseas-stock/v1/ranking/market-cap"
SEC_FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A"})
SEC_DELISTING_FORMS = frozenset({"25", "25-NSE"})


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecFinancialFiling:
    """A domestic SEC financial filing with its primary inline-XBRL document."""

    accession: str
    filing_date: date
    report_date: date | None
    primary_document: str


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
        self._company_ticker_rows_cache: list[tuple[str, str, str]] | None = None
        self._submissions_cache: dict[str, dict[str, Any]] = {}
        self._six_k_documents_cache: dict[tuple[str, str], list[SixKDocument]] = {}

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

    def _get_text(self, url: str, operation: str) -> str:
        remaining = self.interval - (time.monotonic() - self._last_request)
        if self._last_request and remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()
        try:
            payload = bounded_request(
                self.session, "GET", url, provider="SEC EDGAR", operation=operation,
                headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
                total_timeout=45, attempt_timeout=15, connect_timeout=5, read_timeout=25,
                binary=True,
            )
        except Exception as exc:
            raise ProviderError(safe_request_failure("SEC EDGAR", operation, exc)) from None
        self.request_count += 1
        if not isinstance(payload, bytes):
            raise ProviderError(f"SEC EDGAR {operation} returned invalid content")
        return payload.decode("utf-8", errors="replace")

    def company_ticker_rows(self) -> list[tuple[str, str, str]]:
        if self._company_ticker_rows_cache is not None:
            return list(self._company_ticker_rows_cache)
        payload = self._get(SEC_TICKERS_URL, "company tickers")
        result: list[tuple[str, str, str]] = []
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            title = str(row.get("title") or "").strip()
            cik = normalize_cik(row.get("cik_str"))
            if ticker and title and cik:
                result.append((ticker, title, cik))
        self._company_ticker_rows_cache = result
        return list(result)

    def ticker_directory(self) -> dict[str, str]:
        return {ticker: cik for ticker, _, cik in self.company_ticker_rows()}

    def submissions(self, cik: str) -> dict[str, Any]:
        normalized = normalize_cik(cik)
        if normalized not in self._submissions_cache:
            self._submissions_cache[normalized] = self._get(
                f"{SEC_DATA_BASE}/submissions/CIK{normalized}.json", f"submissions {cik}",
            )
        return self._submissions_cache[normalized]

    def company_facts(self, cik: str) -> dict[str, Any]:
        return self._get(f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{normalize_cik(cik)}.json", f"company facts {cik}")

    def financial_filings(self, cik: str, *, filed_from: date, filed_to: date) -> list[SecFinancialFiling]:
        """Return SEC 10-Q/10-K filings filed in the requested window."""
        recent = self.submissions(cik).get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            return []
        result: list[SecFinancialFiling] = []
        columns = zip(
            recent.get("form", []), recent.get("filingDate", []), recent.get("reportDate", []),
            recent.get("accessionNumber", []), recent.get("primaryDocument", []), strict=False,
        )
        for form, filed_on, reported_on, accession, primary_document in columns:
            if str(form).upper() not in SEC_FORMS:
                continue
            try:
                filing_date = date.fromisoformat(str(filed_on))
            except ValueError:
                continue
            if not filed_from <= filing_date <= filed_to:
                continue
            try:
                report_date = date.fromisoformat(str(reported_on))
            except ValueError:
                report_date = None
            document = str(primary_document or "").strip()
            if accession and document:
                result.append(SecFinancialFiling(str(accession), filing_date, report_date, document))
        return sorted({item.accession: item for item in result}.values(), key=lambda item: (item.filing_date, item.accession))

    def inline_xbrl_instance(self, cik: str, filing: SecFinancialFiling) -> str | None:
        """Read the filing's official inline-XBRL instance when companyfacts is stale."""
        normalized = normalize_cik(cik)
        if normalized is None:
            return None
        accession = re.sub(r"\D", "", filing.accession)
        base = f"{SEC_ARCHIVES_BASE}/{int(normalized)}/{accession}"
        directory = self._get(f"{base}/index.json", f"financial filing index {filing.accession}")
        items = directory.get("directory", {}).get("item", []) if isinstance(directory, dict) else []
        names = [
            str(item.get("name") or "") for item in items if isinstance(item, dict)
            and re.fullmatch(r"[A-Za-z0-9_.-]+_htm\.xml", str(item.get("name") or ""), re.I)
        ]
        if not names:
            return None
        name = sorted(names)[0]
        return self._get_text(f"{base}/{name}", f"inline XBRL {filing.accession}")

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

    @staticmethod
    def _six_k_rows(payload: dict[str, Any]) -> list[SixKFiling]:
        recent = payload.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            return []
        result: list[SixKFiling] = []
        columns = zip(
            recent.get("form", []), recent.get("filingDate", []), recent.get("reportDate", []),
            recent.get("accessionNumber", []), recent.get("primaryDocument", []), strict=False,
        )
        for form, filed_on, reported_on, accession, primary_document in columns:
            if str(form).upper() not in {"6-K", "6-K/A"}:
                continue
            try:
                filing_date = date.fromisoformat(str(filed_on))
            except ValueError:
                continue
            try:
                report_date = date.fromisoformat(str(reported_on))
            except ValueError:
                report_date = None
            document = str(primary_document or "").strip()
            if accession and document:
                result.append(SixKFiling(str(accession), filing_date, report_date, document))
        return result

    def six_k_filings(self, cik: str, *, filed_from: date, filed_to: date) -> list[SixKFiling]:
        payload = self.submissions(cik)
        result = self._six_k_rows(payload)
        recent_dates = [item.filing_date for item in result]
        files = payload.get("filings", {}).get("files", [])
        if (not recent_dates or filed_from < min(recent_dates)) and isinstance(files, list):
            for item in files:
                if not isinstance(item, dict):
                    continue
                try:
                    shard_from = date.fromisoformat(str(item.get("filingFrom") or ""))
                    shard_to = date.fromisoformat(str(item.get("filingTo") or ""))
                except ValueError:
                    continue
                if shard_to < filed_from or shard_from > filed_to:
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    result.extend(self._six_k_rows({"filings": {"recent": self._get(
                        f"{SEC_DATA_BASE}/submissions/{name}", f"submissions archive {cik} {name}",
                    )}}))
        return sorted(
            {item.accession: item for item in result if filed_from <= item.filing_date <= filed_to}.values(),
            key=lambda item: (item.filing_date, item.accession),
        )

    def six_k_documents(self, cik: str, filing: SixKFiling) -> list[SixKDocument]:
        normalized = normalize_cik(cik)
        if normalized is None:
            return []
        cache_key = (normalized, filing.accession)
        if cache_key in self._six_k_documents_cache:
            return list(self._six_k_documents_cache[cache_key])
        accession = re.sub(r"\D", "", filing.accession)
        base = f"{SEC_ARCHIVES_BASE}/{int(normalized)}/{accession}"
        primary = self._get_text(f"{base}/{filing.primary_document}", f"6-K {filing.accession}")
        documents = [SixKDocument(filing.primary_document, primary)]
        names = linked_financial_documents(primary)
        earnings_cover = bool(re.search(
            r"(?:reports?|announces?).{0,100}(?:quarter|annual).{0,100}results", primary, re.I | re.S,
        ))
        try:
            directory = self._get(f"{base}/index.json", f"6-K index {filing.accession}")
            items = directory.get("directory", {}).get("item", [])
            for item in items if isinstance(items, list) else []:
                name = str(item.get("name") or "") if isinstance(item, dict) else ""
                compact = re.sub(r"[^a-z0-9]", "", name.lower())
                is_html = name.lower().endswith((".htm", ".html"))
                is_attachment = name != filing.primary_document and "index" not in compact
                if is_html and (
                    any(term in compact for term in (
                        "financialstatement", "financialresult", "pressrelease", "quarterlyresult", "interimresult",
                        "halfyear", "half-year", "result", "ex99", "exhibit99",
                    )) or (earnings_cover and is_attachment)
                ):
                    names.append(name)
        except ProviderError:
            # The filing's own exhibit index remains the authoritative fallback.
            pass
        for name in list(dict.fromkeys(names))[:4]:
            safe_name = name.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.html?", safe_name, re.I):
                continue
            documents.append(SixKDocument(
                safe_name, self._get_text(f"{base}/{safe_name}", f"6-K exhibit {filing.accession} {safe_name}"),
            ))
        self._six_k_documents_cache[cache_key] = documents
        return list(documents)

    def delisting_dates(self, cik: str) -> list[date]:
        """Return official Form 25/25-NSE filing dates indexed for the issuer."""
        recent = self.submissions(cik).get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            return []
        result: list[date] = []
        for form, filed_on in zip(recent.get("form", []), recent.get("filingDate", []), strict=False):
            if str(form).upper() not in SEC_DELISTING_FORMS:
                continue
            try:
                result.append(date.fromisoformat(str(filed_on)))
            except ValueError:
                continue
        return sorted(set(result))
