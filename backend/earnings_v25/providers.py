from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from corporate_events import parse_absorbed_merger, parse_absorbed_merger_archive

from .http import (
    RETRYABLE_STATUS_CODES,
    ExecutionDeadlineExceeded,
    InvalidJsonResponse,
    ResponseDeadlineExceeded,
    bounded_request,
    provider_session,
    safe_request_failure,
)
from .models import DelistingFiling, PeriodicFiling, Security


OPEN_DART_BASE = "https://opendart.fss.or.kr/api"
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
ECOS_KRW_SPECS = {
    "USD": ("0000001", Decimal("1")),
    # ECOS 731Y001의 엔화 값은 100엔당 원화이므로 1엔당 원화로 정규화한다.
    "JPY": ("0000002", Decimal("0.01")),
}
FINANCIAL_COMPANY_FUNCTION = "earnings-financial-company-source"
REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
FINANCIAL_SECTOR_SPECS: dict[str, dict[str, Any]] = {
    "bank": {
        "industry_prefixes": ("641",),
        "title": "은행_재무현황_주요자금조달운용_요약손익계산서(은행)",
        "scope": "OFS",
        "name_field": "bnkSmryPlSbjCdNm",
        "cumulative_field": "bnkSmryPlSbjCmtlAt",
        "standalone_field": "bnkSmryPlSbjThqrAmt",
        "top_line": ("영업수익",),
        "operating_income": ("영업이익",),
        "net_income": ("당기순이익",),
    },
    "holding": {
        "industry_prefixes": ("64992",),
        "title": "금융지주_재무현황_요약연결손익계산서",
        "scope": "CFS",
        "name_field": "smryLnkPlAcitCdNm",
        "cumulative_field": "smryLnkPlAcitCmtlAmt",
        "standalone_field": "smryLnkPlAcitAmt",
        "top_line": ("영업수익",),
        "operating_income": ("영업이익",),
        "net_income": ("연결당기순이익", "총당기순이익"),
    },
    "life": {
        "industry_prefixes": ("65110",),
        "title": "생보_재무현황_요약손익계산서(전체)",
        "scope": "OFS",
        "name_field": "smryPlAcitCdNm",
        "cumulative_field": "smryPlAcitCmtlAmt",
        "standalone_field": "smryPlAcitThqrAmt",
        "top_line_sum": (
            "보험손익_보험영업수익",
            "투자손익_투자영업수익",
            "특별계정손익_특별계정수익",
        ),
        "operating_income": ("영업이익",),
        "net_income": ("당기순이익",),
    },
    "nonlife": {
        "industry_prefixes": ("65121",),
        "title": "손보_재무현황_요약손익계산서(전체)",
        "scope": "OFS",
        "name_field": "smryPlAcitCdNm",
        "cumulative_field": "smryPlAcitCmtlAmt",
        "standalone_field": "smryPlAcitThqrAmt",
        "top_line_sum": ("경과보험료", "투자영업수익", "특별계정이익_특별계정수익"),
        "operating_income": ("총영업이익",),
        "net_income": ("당기순이익(또는 당기순손실)",),
    },
    "card": {
        "industry_prefixes": ("64913",),
        "title": "신용카드_재무현황_요약손익계산서(08.03월이후)",
        "scope": "OFS",
        "name_field": "smryPlAcitCdNm",
        "cumulative_field": "smryPlAcitCmtlAmt",
        "standalone_field": "smryPlAcitThqrAmt",
        "top_line": ("영업수익",),
        "operating_income": ("영업이익",),
        "net_income": ("당기순이익(손실)",),
    },
    "securities": {
        "industry_prefixes": ("66121",),
        "title": "증권_재무현황_요약손익계산서(11.06월이후)",
        "scope": "OFS",
        "name_field": "smryPlAcitCdNm",
        "cumulative_field": "cmtlAmt",
        "standalone_field": "thqrAmt",
        "top_line": ("[영업수익]",),
        "operating_income": ("[영업이익(손실)]",),
        "net_income": ("[당기순이익(손실)]",),
    },
}
KRX_ENDPOINTS = {"kr_largecap": "stk_bydd_trd", "kr_kosdaq": "ksq_bydd_trd"}
DELISTING_TITLES = {
    "상장폐지결정": "decision",
    "상장폐지": "final",
    # 합병으로 소멸하는 상장사의 거래소 최종 공시 제목이다. 예정·우려·
    # 사유발생은 포함하지 않고 실제 폐지만 명시적으로 허용한다.
    "상장폐지(피흡수합병)": "final",
}


class ProviderError(RuntimeError):
    """API 키나 전체 요청 URL을 노출하지 않는 공급자 오류."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class FinancialCompanySnapshot:
    """금융위원회 요약재무제표의 한 보고서·재무제표 범위 원본값."""

    crno: str
    report_code: str
    consolidation_scope: str | None
    currency: str
    top_line_cumulative: Decimal | None
    operating_income_cumulative: Decimal | None
    net_income_cumulative: Decimal | None
    top_line_standalone: Decimal | None = None
    operating_income_standalone: Decimal | None = None
    net_income_standalone: Decimal | None = None


def _retryable_request_error(error: Exception) -> bool:
    status = getattr(getattr(error, "response", None), "status_code", None)
    return (
        isinstance(error, (
            requests.Timeout, requests.ConnectionError,
            ResponseDeadlineExceeded, InvalidJsonResponse,
        ))
        or status in RETRYABLE_STATUS_CODES
    )


def _decimal(value: Any) -> Decimal | None:
    text = "" if value is None else str(value).replace(",", "").replace(" ", "").strip()
    if text in {"", "-", "—", "–"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def normalized_disclosure_title(value: Any) -> str:
    """정정·시장 접두사와 공백만 제거하고 공시 제목 본문은 보존한다."""
    title = str(value or "").strip()
    while re.match(r"^\s*\[[^\]]+\]", title):
        title = re.sub(r"^\s*\[[^\]]+\]\s*", "", title, count=1)
    return re.sub(r"\s+", "", title)


CONNECT_TIMEOUT = 5
STANDARD_READ_TIMEOUT = 20
FAST_READ_TIMEOUT = 12
KRX_TOTAL_TIMEOUT = 30
ECOS_TOTAL_TIMEOUT = 30


def _session() -> requests.Session:
    """공급자 재시도는 bounded_request 한 곳에서만 수행한다."""
    return provider_session()


class OpenDartClient:
    """OpenDART 기업 메타데이터와 구조화 재무제표 API를 담당한다."""

    def __init__(self, api_key: str, *, session: Any | None = None, interval: float = 0.15) -> None:
        if not api_key.strip():
            raise ValueError("OpenDART API key is required")
        self.api_key = api_key.strip()
        self.session = session or _session()
        self.interval = max(interval, 0)
        self._last_request = 0.0
        self.request_count = 0

    @classmethod
    def from_env(cls) -> "OpenDartClient":
        key = os.getenv("OPENDART_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing OPENDART_API_KEY")
        return cls(key)

    def _wait(self) -> None:
        remaining = self.interval - (time.monotonic() - self._last_request)
        if self._last_request and remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()

    @staticmethod
    def _progress(stage: str, **details: Any) -> None:
        print(json.dumps({"stage": stage, **details}, ensure_ascii=False, default=str), flush=True)

    def _retry_progress(self, endpoint: str) -> Callable[[int, str, float | None], None]:
        return lambda attempt, reason, remaining: self._progress(
            "provider_request_retry",
            provider="OpenDART", endpoint=endpoint, attempt=attempt,
            reason=reason, remaining_budget_seconds=remaining,
        )

    def _transport_progress(self, endpoint: str) -> Callable[[str, dict[str, Any]], None]:
        return lambda event, details: self._progress(
            "provider_response_progress",
            provider="OpenDART", endpoint=endpoint, event=event, **details,
        )

    def _get(
        self,
        endpoint: str,
        params: dict[str, str],
        *,
        binary: bool = False,
        retry_total: int | None = None,
        return_status: bool = False,
    ) -> Any:
        self._wait()
        self.request_count += 1
        try:
            payload = bounded_request(
                self.session, "GET",
                f"{OPEN_DART_BASE}/{endpoint}",
                provider="OpenDART", operation=endpoint,
                params={"crtfc_key": self.api_key, **params},
                # 정상적으로 바이트가 수신되는 응답은 애플리케이션 전체 마감선까지
                # 허용한다. 여기서는 연결 실패와 20초 수신 정지만 재시도한다.
                total_timeout=None,
                attempt_timeout=None,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=STANDARD_READ_TIMEOUT,
                binary=binary,
                **({"retry_total": retry_total} if retry_total is not None else {}),
                on_retry=self._retry_progress(endpoint),
                on_progress=self._transport_progress(endpoint),
            )
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            # API 키·URL·응답 본문은 노출하지 않고 실패 종류만 남긴다.
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(
                safe_request_failure("OpenDART", endpoint, exc),
                retryable=_retryable_request_error(exc),
            ) from None
        if binary:
            return payload
        if not isinstance(payload, dict):
            raise ProviderError(f"OpenDART {endpoint} returned invalid JSON")
        status = str(payload.get("status") or "")
        if status == "013":
            empty = {"list": []}
            return (status, empty) if return_status else empty
        if status != "000":
            raise ProviderError(f"OpenDART {endpoint} rejected the request ({status or 'unknown'})")
        return (status, payload) if return_status else payload

    def corporation_map(self) -> dict[str, tuple[str, str]]:
        content = self._get("corpCode.xml", {}, binary=True)
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                root = ET.fromstring(archive.read("CORPCODE.xml"))
        except Exception:
            raise ProviderError("OpenDART corporation-code archive is invalid") from None
        result: dict[str, tuple[str, str]] = {}
        for node in root.findall("list"):
            stock = (node.findtext("stock_code") or "").strip()
            corp = (node.findtext("corp_code") or "").strip()
            name = (node.findtext("corp_name") or "").strip()
            if re.fullmatch(r"\d{6}", stock) and re.fullmatch(r"\d{8}", corp):
                result[stock] = (corp, name)
        return result

    def multi_accounts(self, corp_codes: Iterable[str], year: int, quarter: int) -> list[dict[str, Any]]:
        unique = list(dict.fromkeys(str(code).strip() for code in corp_codes if str(code).strip()))
        rows: list[dict[str, Any]] = []
        batch_count = (len(unique) + 99) // 100
        for start in range(0, len(unique), 100):
            batch = unique[start:start + 100]
            batch_number = start // 100 + 1
            self._progress(
                "open_dart_batch_start", endpoint="fnlttMultiAcnt.json",
                year=year, quarter=quarter, batch=batch_number,
                batch_count=batch_count, company_count=len(batch),
            )
            try:
                # 최대 크기 요청만 두 번 시도한다. 두 번째까지 전송 계층 오류가
                # 반복될 때 해당 묶음만 50+50으로 격리해 한 번씩 호출한다.
                batch_rows = self._multi_account_batch(
                    batch, year, quarter,
                    retry_total=1 if len(batch) == 100 else None,
                )
            except ProviderError as exc:
                if len(batch) != 100 or not exc.retryable:
                    raise
                self._progress(
                    "open_dart_batch_split_start", endpoint="fnlttMultiAcnt.json",
                    year=year, quarter=quarter, batch=batch_number,
                    company_count=len(batch), split_size=50,
                )
                batch_rows = []
                for split_start in range(0, len(batch), 50):
                    split_batch = batch[split_start:split_start + 50]
                    split_number = split_start // 50 + 1
                    split_rows = self._multi_account_batch(
                        split_batch, year, quarter, retry_total=0,
                    )
                    batch_rows.extend(split_rows)
                    self._progress(
                        "open_dart_batch_split_done", endpoint="fnlttMultiAcnt.json",
                        year=year, quarter=quarter, batch=batch_number,
                        split=split_number, split_count=2,
                        company_count=len(split_batch), row_count=len(split_rows),
                    )
            rows.extend(batch_rows)
            self._progress(
                "open_dart_batch_done", endpoint="fnlttMultiAcnt.json",
                year=year, quarter=quarter, batch=batch_number,
                batch_count=batch_count, row_count=len(batch_rows),
            )
        return rows

    def _multi_account_batch(
        self,
        corp_codes: list[str],
        year: int,
        quarter: int,
        *,
        retry_total: int | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._get("fnlttMultiAcnt.json", {
            "corp_code": ",".join(corp_codes),
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter],
        }, retry_total=retry_total)
        return [row for row in payload.get("list", []) if isinstance(row, dict)]

    def company_profile(self, corp_code: str) -> dict[str, str] | None:
        payload = self._get("company.json", {"corp_code": corp_code})
        industry_code = str(payload.get("induty_code") or "").strip()
        jurir_no = re.sub(r"\D", "", str(payload.get("jurir_no") or ""))
        profile: dict[str, str] = {}
        if industry_code:
            profile["industry_code"] = industry_code
        if re.fullmatch(r"\d{13}", jurir_no):
            profile["jurir_no"] = jurir_no
        return profile or None

    def single_accounts(
        self,
        corp_code: str,
        year: int,
        quarter: int,
        consolidation_scope: str,
    ) -> list[dict[str, Any]]:
        scope = consolidation_scope.strip().upper()
        payload = self._get("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter],
            "fs_div": scope,
        })
        rows: list[dict[str, Any]] = []
        for raw in payload.get("list", []):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            # fnlttSinglAcntAll은 fs_div로 범위를 지정해도 응답 행에는
            # fs_div를 싣지 않는다. 요청으로 이미 확정된 범위를 보존해야
            # 공통 변환기가 개별 호출 결과를 같은 계약으로 처리할 수 있다.
            if not str(row.get("fs_div") or "").strip():
                row["fs_div"] = scope
            rows.append(row)
        return rows

    def diagnose_single_accounts(
        self,
        corp_code: str,
        year: int,
        quarter: int,
        consolidation_scope: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """구조화 응답의 상태와 행을 보존하는 V2.5 읽기 전용 진단 경로."""
        scope = consolidation_scope.strip().upper()
        status, payload = self._get("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter],
            "fs_div": scope,
        }, return_status=True)
        rows: list[dict[str, Any]] = []
        for raw in payload.get("list", []):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            if not str(row.get("fs_div") or "").strip():
                row["fs_div"] = scope
            rows.append(row)
        return status, rows

    def periodic_filings(
        self,
        start: date,
        end: date,
        *,
        corp_code: str | None = None,
    ) -> list[PeriodicFiling]:
        """접수번호로 중복을 구분할 수 있는 정기공시 목록을 반환한다."""
        result: dict[str, PeriodicFiling] = {}
        page = 1
        while True:
            params = {
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "A",
                "page_no": str(page),
                "page_count": "100",
            }
            if corp_code:
                params["corp_code"] = corp_code
            payload = self._get("list.json", params)
            items = [row for row in payload.get("list", []) if isinstance(row, dict)]
            for row in items:
                code = str(row.get("corp_code") or "").strip()
                receipt = str(row.get("rcept_no") or "").strip()
                received = str(row.get("rcept_dt") or "").strip()
                report = str(row.get("report_nm") or "")
                if (
                    re.fullmatch(r"\d{8}", code)
                    and re.fullmatch(r"\d{14}", receipt)
                    and re.fullmatch(r"\d{8}", received)
                    and any(name in report for name in ("분기보고서", "반기보고서", "사업보고서"))
                ):
                    result[receipt] = PeriodicFiling(
                        corp_code=code,
                        receipt_no=receipt,
                        received_on=date.fromisoformat(f"{received[:4]}-{received[4:6]}-{received[6:]}"),
                        report_name=report,
                    )
            total_pages = int(payload.get("total_page") or 1)
            if page >= total_pages:
                break
            page += 1
        return sorted(result.values(), key=lambda row: (row.received_on, row.receipt_no))

    def filing_archive(self, receipt_no: str) -> bytes:
        """Download an accepted filing through OpenDART document.xml."""
        receipt = str(receipt_no).strip()
        if not re.fullmatch(r"\d{14}", receipt):
            raise ValueError(f"Invalid OpenDART receipt number: {receipt_no!r}")
        content = self._get(
            "document.xml", {"rcept_no": receipt}, binary=True,
        )
        if not isinstance(content, bytes) or not content.startswith(b"PK"):
            raise ProviderError("OpenDART document.xml returned no ZIP archive")
        return content

    def delisting_filings(
        self,
        start: date,
        end: date,
        *,
        corp_code: str | None = None,
    ) -> list[DelistingFiling]:
        """거래소공시 중 상장폐지 결정·확정 제목만 완전 일치로 반환한다."""
        result: dict[str, DelistingFiling] = {}
        page = 1
        while True:
            params = {
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "I",
                "page_no": str(page),
                "page_count": "100",
            }
            if corp_code:
                params["corp_code"] = corp_code
            payload = self._get("list.json", params)
            items = [row for row in payload.get("list", []) if isinstance(row, dict)]
            for row in items:
                code = str(row.get("corp_code") or "").strip()
                receipt = str(row.get("rcept_no") or "").strip()
                received = str(row.get("rcept_dt") or "").strip()
                report = str(row.get("report_nm") or "").strip()
                event_type = DELISTING_TITLES.get(normalized_disclosure_title(report))
                if (
                    event_type is not None
                    and (corp_code is None or code == corp_code)
                    and re.fullmatch(r"\d{8}", code)
                    and re.fullmatch(r"\d{14}", receipt)
                    and re.fullmatch(r"\d{8}", received)
                ):
                    result[receipt] = DelistingFiling(
                        corp_code=code,
                        receipt_no=receipt,
                        received_on=date.fromisoformat(
                            f"{received[:4]}-{received[4:6]}-{received[6:]}"
                        ),
                        report_name=report,
                        event_type=event_type,
                    )
            total_pages = int(payload.get("total_page") or 1)
            if page >= total_pages:
                break
            page += 1
        return sorted(result.values(), key=lambda row: (row.received_on, row.receipt_no))

    def merger_decision_corp_codes(self, start: date, end: date) -> set[str]:
        """기간 중 회사합병 결정 공시를 낸 회사 코드만 가볍게 찾는다."""
        result: set[str] = set()
        page = 1
        while True:
            payload = self._get("list.json", {
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "B",
                "page_no": str(page),
                "page_count": "100",
            })
            items = [row for row in payload.get("list", []) if isinstance(row, dict)]
            for row in items:
                code = str(row.get("corp_code") or "").strip()
                title = normalized_disclosure_title(row.get("report_nm"))
                if re.fullmatch(r"\d{8}", code) and title.endswith("회사합병결정)"):
                    result.add(code)
            total_pages = int(payload.get("total_page") or 1)
            if page >= total_pages:
                break
            page += 1
        return result

    def _merger_decision_disclosures(
        self, start: date, end: date, *, corp_code: str,
    ) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            payload = self._get("list.json", {
                "corp_code": corp_code,
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "B",
                "page_no": str(page),
                "page_count": "100",
            })
            for row in payload.get("list", []):
                if not isinstance(row, dict):
                    continue
                receipt = str(row.get("rcept_no") or "").strip()
                title = normalized_disclosure_title(row.get("report_nm"))
                if re.fullmatch(r"\d{14}", receipt) and title.endswith("회사합병결정)"):
                    rows[receipt] = row
            total_pages = int(payload.get("total_page") or 1)
            if page >= total_pages:
                break
            page += 1
        return sorted(rows.values(), key=lambda row: str(row.get("rcept_no") or ""), reverse=True)

    def absorbed_merger_filings(
        self, start: date, end: date, *, corp_code: str,
    ) -> list[DelistingFiling]:
        """구조화 합병 공시 중 공시회사 자신이 소멸하는 건만 반환한다."""
        payload = self._get("cmpMgDecsn.json", {
            "corp_code": corp_code,
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
        })
        result: dict[str, DelistingFiling] = {}
        for row in payload.get("list", []):
            if not isinstance(row, dict):
                continue
            parsed = parse_absorbed_merger(row, expected_corp_code=corp_code)
            if parsed is None:
                continue
            result[parsed.receipt_no] = DelistingFiling(
                corp_code=parsed.corp_code,
                receipt_no=parsed.receipt_no,
                received_on=parsed.received_on,
                report_name=parsed.report_name,
                event_type="absorbed_merger",
                effective_on=parsed.effective_on,
            )
        if not result:
            for disclosure in self._merger_decision_disclosures(
                start, end, corp_code=corp_code,
            ):
                title = str(disclosure.get("report_nm") or "")
                if title.lstrip().startswith("[첨부정정]"):
                    continue
                receipt = str(disclosure.get("rcept_no") or "").strip()
                try:
                    archive = self._get(
                        "document.xml", {"rcept_no": receipt}, binary=True,
                    )
                except ProviderError:
                    continue
                parsed = parse_absorbed_merger_archive(
                    archive,
                    expected_corp_code=corp_code,
                    corp_name=str(disclosure.get("corp_name") or ""),
                    receipt_no=receipt,
                )
                if parsed is None:
                    continue
                result[parsed.receipt_no] = DelistingFiling(
                    corp_code=parsed.corp_code,
                    receipt_no=parsed.receipt_no,
                    received_on=parsed.received_on,
                    report_name=parsed.report_name,
                    event_type="absorbed_merger",
                    effective_on=parsed.effective_on,
                )
                break
        return sorted(result.values(), key=lambda row: (row.event_on, row.receipt_no))


def _financial_company_report_code(row: dict[str, Any]) -> str | None:
    """금융위 API의 보고서 코드를 OpenDART와 같은 분기 식별자로 정규화한다."""
    report_code = str(row.get("rptCd") or "").strip()
    if report_code in REPORT_CODES.values():
        return report_code
    report_name = str(row.get("rptCdNm") or "")
    month_match = re.search(r"(?:^|[^0-9])(03|06|09|12)(?:[^0-9]|$)", report_name)
    if month_match is None:
        return None
    return {"03": "11013", "06": "11012", "09": "11014", "12": "11011"}[month_match.group(1)]


def _financial_company_scope(row: dict[str, Any]) -> str | None:
    """명시된 연결·별도 구분만 사용한다. 알 수 없는 범위는 추측하지 않는다."""
    value = " ".join(
        str(row.get(field) or "")
        for field in ("fnclDcd", "fnclDcdNm")
    ).upper()
    if "CFS" in value or "연결" in value:
        return "CFS"
    if "OFS" in value or "별도" in value or "개별" in value:
        return "OFS"
    return None


class FinancialCompanyClient:
    """금융위 키는 GitHub Actions secret에서 받아 보호된 프록시에만 전달한다."""

    def __init__(
        self,
        supabase_url: str,
        service_key: str,
        internal_token: str,
        public_data_key: str,
        *,
        session: Any | None = None,
    ) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.service_key = service_key.strip()
        self.internal_token = internal_token.strip()
        self.public_data_key = public_data_key.strip()
        if not self.supabase_url or not self.service_key or not self.internal_token or not self.public_data_key:
            raise ValueError("Supabase URL, service key, internal token, and public-data key are required for financial-company lookup")
        self.session = session or _session()
        self.request_count = 0
        self._sector_rows_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    @classmethod
    def from_env(cls) -> "FinancialCompanyClient | None":
        url = os.getenv("SUPABASE_URL", "").strip()
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        internal_token = os.getenv("EARNINGS_FINANCIAL_SOURCE_TOKEN", "").strip()
        public_data_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
        return cls(url, service_key, internal_token, public_data_key) if url and service_key and internal_token and public_data_key else None

    @staticmethod
    def _sector_for_industry(industry_code: str | None) -> str | None:
        code = str(industry_code or "").strip()
        for sector, spec in FINANCIAL_SECTOR_SPECS.items():
            if any(code.startswith(prefix) for prefix in spec["industry_prefixes"]):
                return sector
        return None

    def _source_request(
        self,
        payload: dict[str, Any],
        *,
        operation: str,
        total_timeout: float = 30,
        attempt_timeout: float = 12,
    ) -> dict[str, Any]:
        self.request_count += 1
        try:
            result = bounded_request(
                self.session,
                "POST",
                f"{self.supabase_url}/functions/v1/{FINANCIAL_COMPANY_FUNCTION}",
                provider="Financial Services Commission",
                operation=operation,
                headers={
                    "Authorization": f"Bearer {self.internal_token}",
                    "apikey": self.service_key,
                    "Content-Type": "application/json",
                    "X-Public-Data-API-Key": self.public_data_key,
                },
                json=payload,
                total_timeout=total_timeout,
                attempt_timeout=attempt_timeout,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=STANDARD_READ_TIMEOUT,
                on_retry=lambda attempt, reason, remaining: self._progress(
                    "provider_request_retry",
                    provider="Financial Services Commission", endpoint=operation,
                    attempt=attempt, reason=reason, remaining_budget_seconds=remaining,
                ),
            )
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            raise ProviderError(
                safe_request_failure("Financial Services Commission", operation, exc),
                retryable=_retryable_request_error(exc),
            ) from None
        if not isinstance(result, dict):
            raise ProviderError("Financial Services Commission returned invalid JSON")
        return result

    def _sector_rows(self, sector: str, base_month: str) -> list[dict[str, Any]]:
        cache_key = (sector, base_month)
        cached = self._sector_rows_cache.get(cache_key)
        if cached is not None:
            return cached

        spec = FINANCIAL_SECTOR_SPECS[sector]
        rows: list[dict[str, Any]] = []
        seen_pages: set[tuple[str, str, str, int]] = set()
        for page_no in range(1, 6):
            payload = self._source_request(
                {
                    "mode": "sector_financial",
                    "sector": sector,
                    "bas_ym": base_month,
                    "title": spec["title"],
                    "num_of_rows": 9999,
                    "page_no": page_no,
                },
                operation=f"sector-financial:{sector}:{page_no}",
                total_timeout=60,
                attempt_timeout=30,
            )
            status = str(payload.get("status") or "")
            if status == "no_report":
                break
            if status != "ok":
                raise ProviderError("Financial Services Commission rejected the sector request")
            raw_rows = payload.get("rows")
            if not isinstance(raw_rows, list):
                raise ProviderError("Financial Services Commission returned invalid sector data")
            page_rows = [row for row in raw_rows if isinstance(row, dict)]
            if not page_rows:
                break
            signature = (
                str(page_rows[0].get("crno") or ""),
                str(page_rows[-1].get("crno") or ""),
                str(page_rows[-1].get(spec["name_field"]) or ""),
                len(page_rows),
            )
            if signature in seen_pages:
                break
            seen_pages.add(signature)
            rows.extend(page_rows)
            if len(page_rows) < 9999:
                break
        self._sector_rows_cache[cache_key] = rows
        return rows

    @staticmethod
    def _sector_amount(
        account_rows: dict[str, dict[str, Any]],
        labels: tuple[str, ...],
        field: str,
    ) -> Decimal | None:
        for label in labels:
            if label in account_rows:
                return _decimal(account_rows[label].get(field))
        return None

    @staticmethod
    def _sector_sum(
        account_rows: dict[str, dict[str, Any]],
        labels: tuple[str, ...],
        field: str,
    ) -> Decimal | None:
        values = [
            _decimal(account_rows[label].get(field))
            for label in labels
            if label in account_rows
        ]
        if len(values) != len(labels) or any(value is None for value in values):
            return None
        return sum((value for value in values if value is not None), Decimal(0))

    def _sector_quarter_financials(
        self,
        crno: str,
        year: int,
        quarter: int,
        sector: str,
    ) -> list[FinancialCompanySnapshot]:
        spec = FINANCIAL_SECTOR_SPECS[sector]
        base_month = f"{year}{quarter * 3:02d}"
        rows = [
            row for row in self._sector_rows(sector, base_month)
            if str(row.get("crno") or "").strip() == crno
            and str(row.get("basYm") or "").strip() == base_month
        ]
        account_rows = {
            str(row.get(spec["name_field"]) or "").strip(): row
            for row in rows
            if str(row.get(spec["name_field"]) or "").strip()
        }
        if not account_rows:
            return []

        cumulative_field = str(spec["cumulative_field"])
        standalone_field = str(spec["standalone_field"])
        top_sum = spec.get("top_line_sum")
        if isinstance(top_sum, tuple):
            top_line_cumulative = self._sector_sum(account_rows, top_sum, cumulative_field)
            top_line_standalone = self._sector_sum(account_rows, top_sum, standalone_field)
        else:
            labels = spec["top_line"]
            top_line_cumulative = self._sector_amount(account_rows, labels, cumulative_field)
            top_line_standalone = self._sector_amount(account_rows, labels, standalone_field)

        return [FinancialCompanySnapshot(
            crno=crno,
            report_code=REPORT_CODES[quarter],
            consolidation_scope=str(spec["scope"]),
            currency="KRW",
            top_line_cumulative=top_line_cumulative,
            operating_income_cumulative=self._sector_amount(
                account_rows, spec["operating_income"], cumulative_field,
            ),
            net_income_cumulative=self._sector_amount(
                account_rows, spec["net_income"], cumulative_field,
            ),
            top_line_standalone=top_line_standalone,
            operating_income_standalone=self._sector_amount(
                account_rows, spec["operating_income"], standalone_field,
            ),
            net_income_standalone=self._sector_amount(
                account_rows, spec["net_income"], standalone_field,
            ),
        )]

    def quarter_financial_candidates(
        self, crno: str, year: int, quarter: int,
        industry_code: str | None = None, *, preferred_scope: str | None = None,
    ) -> list[FinancialCompanySnapshot]:
        """V2.5-only: supplement partial sector reports with the common API."""
        sector = self._sector_for_industry(industry_code)
        candidates = self._sector_quarter_financials(crno, year, quarter, sector) if sector else []
        fields = ("top_line", "operating_income", "net_income")
        if any(
            (preferred_scope is None or item.consolidation_scope == preferred_scope)
            and all(getattr(item, f"{field}_cumulative") is not None
                    or getattr(item, f"{field}_standalone") is not None for field in fields)
            for item in candidates
        ):
            return candidates
        common = self.quarter_financials(crno, year, quarter, None)
        if any(item.crno != crno for item in common):
            raise ProviderError("Financial Services Commission returned a different company")
        return candidates + common

    def quarter_financials(
        self,
        crno: str,
        year: int,
        quarter: int,
        industry_code: str | None = None,
    ) -> list[FinancialCompanySnapshot]:
        sector = self._sector_for_industry(industry_code)
        if sector is not None:
            sector_snapshots = self._sector_quarter_financials(
                crno, year, quarter, sector,
            )
            if sector_snapshots:
                return sector_snapshots

        payload = self._source_request(
            {"crno": crno, "fiscal_year": year},
            operation="financial-company-source",
        )
        status = str(payload.get("status") or "")
        if status in {"not_found", "ambiguous", "no_report"}:
            return []
        if status != "ok":
            raise ProviderError("Financial Services Commission rejected the request")

        returned_crno = str(payload.get("crno") or "").strip()
        reports = payload.get("reports")
        if not re.fullmatch(r"\d{13}", returned_crno) or not isinstance(reports, list):
            raise ProviderError("Financial Services Commission returned invalid financial-company data")
        target_report = REPORT_CODES[quarter]
        snapshots: list[FinancialCompanySnapshot] = []
        for raw in reports:
            if not isinstance(raw, dict) or _financial_company_report_code(raw) != target_report:
                continue
            currency = str(raw.get("curCd") or "KRW").strip().upper()
            snapshots.append(FinancialCompanySnapshot(
                crno=returned_crno,
                report_code=target_report,
                consolidation_scope=_financial_company_scope(raw),
                currency=currency or "KRW",
                top_line_cumulative=_decimal(raw.get("fncoSaleAmt")),
                operating_income_cumulative=_decimal(raw.get("fncoBzopPft")),
                net_income_cumulative=_decimal(raw.get("fncoCrtmNpf")),
            ))
        return snapshots

    @staticmethod
    def _progress(stage: str, **details: Any) -> None:
        print(json.dumps({"stage": stage, **details}, ensure_ascii=False, default=str), flush=True)


class KrxClient:
    def __init__(self, auth_key: str, *, session: Any | None = None) -> None:
        if not auth_key.strip():
            raise ValueError("KRX OPEN API key is required")
        self.auth_key = auth_key.strip()
        self.session = session or _session()
        self.request_count = 0

    @classmethod
    def from_env(cls) -> "KrxClient":
        key = os.getenv("KRX_OPEN_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing KRX_OPEN_API_KEY")
        return cls(key)

    def daily_market(self, market_id: str, day: date) -> list[Security]:
        self.request_count += 1
        endpoint = KRX_ENDPOINTS[market_id]
        try:
            payload = bounded_request(
                self.session, "GET",
                f"{KRX_BASE}/{endpoint}",
                provider="KRX", operation=endpoint,
                params={"basDd": day.strftime("%Y%m%d")},
                headers={"AUTH_KEY": self.auth_key, "Accept": "application/json"},
                total_timeout=KRX_TOTAL_TIMEOUT,
                attempt_timeout=9,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=STANDARD_READ_TIMEOUT,
            )
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            raise ProviderError(safe_request_failure("KRX", endpoint, exc)) from None
        raw_rows = payload.get("OutBlock_1") if isinstance(payload, dict) else None
        if not isinstance(raw_rows, list):
            raise ProviderError(f"KRX {endpoint} returned invalid data")
        rows: list[Security] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            code_text = str(raw.get("ISU_CD") or "").strip()
            match = re.search(r"KR\d(\d{6})", code_text)
            stock_code = code_text if re.fullmatch(r"\d{6}", code_text) else (match.group(1) if match else "")
            cap = _decimal(raw.get("MKTCAP"))
            if not stock_code or cap is None or cap < 0:
                continue
            rows.append(Security(stock_code, str(raw.get("ISU_NM") or "").strip(), cap, day))
        return sorted(rows, key=lambda row: (-row.market_cap, row.stock_code))

    def last_trading_day(self, market_id: str, on_or_before: date) -> tuple[date, list[Security]]:
        candidate = on_or_before
        for _ in range(15):
            rows = self.daily_market(market_id, candidate)
            if rows:
                return candidate, rows
            candidate -= timedelta(days=1)
        raise ProviderError(f"KRX has no trading day near {on_or_before.isoformat()}")


class EcosFxClient:
    """기준일 이전의 최근 외화/KRW 종가를 1 외화 단위 기준으로 조회한다."""

    def __init__(self, api_key: str, *, session: Any | None = None) -> None:
        if not api_key.strip():
            raise ValueError("ECOS API key is required")
        self.api_key = api_key.strip()
        self.session = session or _session()
        self.cache: dict[tuple[str, date], tuple[date, Decimal]] = {}
        self.request_count = 0

    def latest_krw(self, base_currency: str, reference_date: date) -> tuple[date, Decimal]:
        base_currency = base_currency.upper()
        spec = ECOS_KRW_SPECS.get(base_currency)
        if spec is None:
            raise ProviderError(f"ECOS does not support {base_currency}/KRW")
        cache_key = (base_currency, reference_date)
        if cache_key in self.cache:
            return self.cache[cache_key]

        item_code, unit_multiplier = spec
        operation = f"{base_currency}/KRW"
        start = reference_date - timedelta(days=10)
        url = (
            f"{ECOS_BASE}/{self.api_key}/json/kr/1/100/731Y001/D/"
            f"{start:%Y%m%d}/{reference_date:%Y%m%d}/{item_code}"
        )
        self.request_count += 1
        try:
            payload = bounded_request(
                self.session, "GET", url,
                provider="ECOS", operation=operation,
                total_timeout=ECOS_TOTAL_TIMEOUT,
                attempt_timeout=9,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=15,
            )
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            raise ProviderError(safe_request_failure("ECOS", operation, exc)) from None
        if not isinstance(payload, dict):
            raise ProviderError(f"ECOS {operation} returned invalid JSON")
        result = payload.get("RESULT")
        if isinstance(result, dict):
            code = re.sub(r"[^0-9A-Za-z_-]", "", str(result.get("CODE") or "unknown"))
            raise ProviderError(f"ECOS {operation} rejected the request ({code})")
        statistic = payload.get("StatisticSearch")
        rows = statistic.get("row") if isinstance(statistic, dict) else None
        if not isinstance(rows, list):
            raise ProviderError(f"ECOS {operation} returned invalid data")
        candidates: list[tuple[date, Decimal]] = []
        for row in rows:
            value = _decimal(row.get("DATA_VALUE"))
            observed = str(row.get("TIME") or "")
            if (
                value is not None and value > 0
                and re.fullmatch(r"\d{8}", observed)
                and observed <= reference_date.strftime("%Y%m%d")
            ):
                normalized = value * unit_multiplier
                candidates.append((
                    date.fromisoformat(f"{observed[:4]}-{observed[4:6]}-{observed[6:]}"),
                    normalized,
                ))
        if not candidates:
            raise ProviderError(f"ECOS returned no {operation} value near quarter end")
        latest = max(candidates, key=lambda item: item[0])
        self.cache[cache_key] = latest
        return latest

    def latest_usd_krw(self, reference_date: date) -> tuple[date, Decimal]:
        return self.latest_krw("USD", reference_date)

    def latest_jpy_krw(self, reference_date: date) -> tuple[date, Decimal]:
        return self.latest_krw("JPY", reference_date)
