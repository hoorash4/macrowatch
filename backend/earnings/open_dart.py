"""Small, testable OpenDART HTTP client.

The client only handles provider transport and pagination. Database writes and
financial-account interpretation stay outside it so retries cannot partially
mutate canonical earnings data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
import re
from typing import Any, Iterable, Iterator, Sequence


OPEN_DART_BASE_URL = "https://opendart.fss.or.kr/api"
DART_BASE_URL = "https://dart.fss.or.kr"
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
        try:
            payload = response.json()
        except Exception:
            raise OpenDartApiError("invalid_json", f"{endpoint} returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise OpenDartApiError("invalid_json", "Response root must be an object")
        status = str(payload.get("status", ""))
        if status == OPEN_DART_NO_DATA:
            payload = {**payload, "list": []}
        elif status != "000":
            message = str(payload.get("message", "Unknown error")).replace(self.api_key, "[redacted]")
            raise OpenDartApiError(status or "unknown", message)
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

    def fetch_filing_archive(self, receipt_number: str) -> OpenDartBinaryResponse:
        """Download an official filing archive through the authenticated API."""
        receipt = self._validate_receipt_number(receipt_number)
        try:
            response = self.session.get(
                f"{OPEN_DART_BASE_URL}/document.xml",
                params={"crtfc_key": self.api_key, "rcept_no": receipt},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            raise OpenDartApiError(
                "transport_error", "document.xml request failed"
            ) from None
        content = bytes(response.content)
        if not content.startswith(b"PK"):
            decoded = content.decode("utf-8", errors="replace")
            status_match = re.search(r"<status>([^<]+)</status>", decoded)
            message_match = re.search(r"<message>([^<]+)</message>", decoded)
            status = status_match.group(1) if status_match else "invalid_archive"
            message = message_match.group(1) if message_match else "document.xml returned no ZIP file"
            raise OpenDartApiError(status, message.replace(self.api_key, "[redacted]"))
        return OpenDartBinaryResponse(
            "document.xml", {"rcept_no": receipt}, content
        )

    def fetch_financial_xbrl_archive(
        self,
        receipt_number: str,
        report_code: str,
    ) -> OpenDartBinaryResponse:
        """Download the official XBRL financial-statement ZIP."""
        receipt = self._validate_receipt_number(receipt_number)
        _year, code = self._validate_period(2015, report_code)
        try:
            response = self.session.get(
                f"{OPEN_DART_BASE_URL}/fnlttXbrl.xml",
                params={
                    "crtfc_key": self.api_key,
                    "rcept_no": receipt,
                    "reprt_code": code,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            raise OpenDartApiError(
                "transport_error", "fnlttXbrl.xml request failed"
            ) from None
        content = bytes(response.content)
        if not content.startswith(b"PK"):
            decoded = content.decode("utf-8", errors="replace")
            status_match = re.search(r"<status>([^<]+)</status>", decoded)
            message_match = re.search(r"<message>([^<]+)</message>", decoded)
            status = status_match.group(1) if status_match else "invalid_archive"
            message = (
                message_match.group(1)
                if message_match else "fnlttXbrl.xml returned no ZIP file"
            )
            raise OpenDartApiError(
                status, message.replace(self.api_key, "[redacted]")
            )
        return OpenDartBinaryResponse(
            "fnlttXbrl.xml",
            {"rcept_no": receipt, "reprt_code": code},
            content,
        )

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

    @staticmethod
    def _validate_receipt_number(receipt_number: str) -> str:
        receipt = str(receipt_number).strip()
        if len(receipt) != 14 or not receipt.isdigit():
            raise ValueError("DART receipt_number must contain fourteen digits.")
        return receipt

    def _get_public_document(
        self,
        endpoint: str,
        params: dict[str, str],
    ) -> OpenDartBinaryResponse:
        """Read one public DART filing page without putting credentials in its URL."""
        try:
            response = self.session.get(
                f"{DART_BASE_URL}/{endpoint}",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception:
            raise OpenDartApiError(
                "transport_error", f"{endpoint} request failed"
            ) from None
        content = bytes(response.content)
        if not content:
            raise OpenDartApiError("empty_document", f"{endpoint} returned an empty document")
        return OpenDartBinaryResponse(endpoint, dict(params), content)

    def fetch_filing_page(self, receipt_number: str) -> OpenDartBinaryResponse:
        """Fetch the public filing index that identifies statement document nodes."""
        receipt = self._validate_receipt_number(receipt_number)
        return self._get_public_document("dsaf001/main.do", {"rcpNo": receipt})

    def fetch_statement_page(
        self,
        receipt_number: str,
        *,
        document_number: str,
        element_id: str,
        offset: str,
        length: str,
        dtd: str = "dart4.xsd",
    ) -> OpenDartBinaryResponse:
        """Fetch one rendered financial-statement node from a public filing."""
        receipt = self._validate_receipt_number(receipt_number)
        numeric = {
            "dcmNo": str(document_number).strip(),
            "eleId": str(element_id).strip(),
            "offset": str(offset).strip(),
            "length": str(length).strip(),
        }
        if any(not value.isdigit() for value in numeric.values()):
            raise ValueError("DART statement document parameters must be numeric.")
        return self._get_public_document(
            "report/viewer.do",
            {"rcpNo": receipt, **numeric, "dtd": str(dtd).strip() or "dart4.xsd"},
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
