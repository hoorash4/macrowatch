"""Small, testable OpenDART HTTP client.

The client only handles provider transport and pagination. Database writes and
financial-account interpretation stay outside it so retries cannot partially
mutate canonical earnings data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from typing import Any, Iterable, Iterator, Sequence


OPEN_DART_BASE_URL = "https://opendart.fss.or.kr/api"
OPEN_DART_MAX_COMPANIES = 100
OPEN_DART_REPORT_CODES = {"11013", "11012", "11014", "11011"}
OPEN_DART_NO_DATA = "013"


class OpenDartApiError(RuntimeError):
    """OpenDART returned a provider or HTTP error."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(f"OpenDART {status}: {message}")
        self.status = status
        self.message = message


@dataclass(frozen=True)
class OpenDartResponse:
    """One raw response plus its secret-free request identity."""

    endpoint: str
    request_params: dict[str, str]
    payload: dict[str, Any]

    @property
    def rows(self) -> list[dict[str, Any]]:
        rows = self.payload.get("list") or []
        return [row for row in rows if isinstance(row, dict)]


@dataclass(frozen=True)
class OpenDartBinaryResponse:
    """One binary response plus its secret-free request identity."""

    endpoint: str
    request_params: dict[str, str]
    content: bytes


def _unique_company_codes(corp_codes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_code in corp_codes:
        code = str(raw_code).strip()
        if len(code) != 8 or not code.isdigit():
            raise ValueError(f"Invalid OpenDART corp_code: {raw_code!r}")
        if code not in seen:
            result.append(code)
            seen.add(code)
    if not result:
        raise ValueError("At least one OpenDART corp_code is required.")
    return result


def chunk_company_codes(
    corp_codes: Iterable[str],
    chunk_size: int = OPEN_DART_MAX_COMPANIES,
) -> list[list[str]]:
    """Deduplicate codes and split them within OpenDART's 100-company limit."""
    if chunk_size < 1 or chunk_size > OPEN_DART_MAX_COMPANIES:
        raise ValueError("OpenDART chunk_size must be between 1 and 100.")
    codes = _unique_company_codes(corp_codes)
    return [codes[index:index + chunk_size] for index in range(0, len(codes), chunk_size)]


class OpenDartClient:
    """OpenDART client using the multi-company endpoint as the default path."""

    def __init__(
        self,
        api_key: str,
        *,
        session: Any | None = None,
        timeout: int = 30,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("OpenDART API key is required.")
        self.api_key = key
        self.timeout = timeout
        self.session = session or self._build_session()

    @classmethod
    def from_env(cls, **kwargs: Any) -> "OpenDartClient":
        key = os.getenv("OPENDART_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing required environment variable: OPENDART_API_KEY")
        return cls(key, **kwargs)

    @staticmethod
    def _build_session() -> Any:
        # Import lazily so the pure parser and fake-session tests do not require
        # the HTTP runtime. Production installs requests from requirements.txt.
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=4,
            connect=4,
            read=4,
            backoff_factor=0.8,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        session = requests.Session()
        session.headers.update({"User-Agent": "MacroWatch/1.0 earnings-data collector"})
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    @staticmethod
    def _validate_period(business_year: int | str, report_code: str) -> tuple[str, str]:
        year = str(business_year).strip()
        code = str(report_code).strip()
        if len(year) != 4 or not year.isdigit() or int(year) < 2015:
            raise ValueError("OpenDART financial data requires a business year from 2015 onward.")
        if code not in OPEN_DART_REPORT_CODES:
            raise ValueError(f"Unsupported OpenDART report code: {report_code!r}")
        return year, code

    def _get_json(self, endpoint: str, params: dict[str, str]) -> OpenDartResponse:
        secret_free_params = dict(params)
        try:
            response = self.session.get(
                f"{OPEN_DART_BASE_URL}/{endpoint}",
                params={"crtfc_key": self.api_key, **params},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            # requests includes the fully expanded URL in transport errors.
            # Replacing it here prevents crtfc_key from reaching CI logs.
            raise OpenDartApiError("transport_error", f"{endpoint} request failed") from None
        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenDartApiError("invalid_json", "Response root must be an object")
        status = str(payload.get("status", ""))
        if status == OPEN_DART_NO_DATA:
            payload = {**payload, "list": []}
        elif status != "000":
            raise OpenDartApiError(status or "unknown", str(payload.get("message", "Unknown error")))
        return OpenDartResponse(endpoint, secret_free_params, payload)

    def fetch_corp_code_archive(self) -> OpenDartBinaryResponse:
        """Download the official corp_code/stock_code mapping archive."""
        try:
            response = self.session.get(
                f"{OPEN_DART_BASE_URL}/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            raise OpenDartApiError("transport_error", "corpCode.xml request failed") from None
        content = bytes(response.content)
        if not content:
            raise OpenDartApiError("empty_archive", "OpenDART corp-code archive is empty")
        return OpenDartBinaryResponse("corpCode.xml", {}, content)

    def fetch_multi_accounts(
        self,
        corp_codes: Sequence[str],
        business_year: int | str,
        report_code: str,
    ) -> OpenDartResponse:
        """Fetch one batch of at most 100 companies' major accounts."""
        year, code = self._validate_period(business_year, report_code)
        companies = _unique_company_codes(corp_codes)
        if len(companies) > OPEN_DART_MAX_COMPANIES:
            raise ValueError("OpenDART multi-company requests allow at most 100 companies.")
        return self._get_json(
            "fnlttMultiAcnt.json",
            {"corp_code": ",".join(companies), "bsns_year": year, "reprt_code": code},
        )

    def fetch_multi_accounts_batched(
        self,
        corp_codes: Iterable[str],
        business_year: int | str,
        report_code: str,
    ) -> Iterator[OpenDartResponse]:
        """Use the multi-company API for any universe size without over-limit calls."""
        for batch in chunk_company_codes(corp_codes):
            yield self.fetch_multi_accounts(batch, business_year, report_code)

    def fetch_single_all_accounts(
        self,
        corp_code: str,
        business_year: int | str,
        report_code: str,
        consolidation_scope: str,
    ) -> OpenDartResponse:
        """Fallback for accounts missing from a multi-company response."""
        year, code = self._validate_period(business_year, report_code)
        company = _unique_company_codes([corp_code])[0]
        scope = consolidation_scope.strip().upper()
        if scope not in {"CFS", "OFS"}:
            raise ValueError("OpenDART consolidation_scope must be CFS or OFS.")
        return self._get_json(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": company,
                "bsns_year": year,
                "reprt_code": code,
                "fs_div": scope,
            },
        )

    def iter_periodic_filings(
        self,
        begin_date: date,
        end_date: date,
        *,
        corp_code: str | None = None,
    ) -> Iterator[OpenDartResponse]:
        """Page through periodic filings, including corrections and amendments."""
        if end_date < begin_date:
            raise ValueError("OpenDART end_date must not precede begin_date.")
        company = _unique_company_codes([corp_code])[0] if corp_code else None
        page = 1
        while True:
            params = {
                "bgn_de": begin_date.strftime("%Y%m%d"),
                "end_de": end_date.strftime("%Y%m%d"),
                "last_reprt_at": "N",
                "pblntf_ty": "A",
                "sort": "date",
                "sort_mth": "asc",
                "page_no": str(page),
                "page_count": "100",
            }
            if company:
                params["corp_code"] = company
            result = self._get_json("list.json", params)
            yield result
            total_pages = int(result.payload.get("total_page") or 1)
            if page >= total_pages:
                break
            page += 1
