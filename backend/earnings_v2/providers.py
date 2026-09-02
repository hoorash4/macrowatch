from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .http import (
    ExecutionDeadlineExceeded,
    bounded_request,
    provider_session,
    safe_request_failure,
)
from .models import PeriodicFiling, Security


OPEN_DART_BASE = "https://opendart.fss.or.kr/api"
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
KIS_BASE = "https://openapi.koreainvestment.com:9443"
ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
KRX_ENDPOINTS = {"kr_largecap": "stk_bydd_trd", "kr_kosdaq": "ksq_bydd_trd"}


class ProviderError(RuntimeError):
    """API 키나 전체 요청 URL을 노출하지 않는 공급자 오류."""


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


CONNECT_TIMEOUT = 5
STANDARD_READ_TIMEOUT = 20
FAST_READ_TIMEOUT = 12
KRX_TOTAL_TIMEOUT = 30
KIS_TOTAL_TIMEOUT = 25
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

    def _get(self, endpoint: str, params: dict[str, str], *, binary: bool = False) -> Any:
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
                on_retry=self._retry_progress(endpoint),
                on_progress=self._transport_progress(endpoint),
            )
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            # API 키·URL·응답 본문은 노출하지 않고 실패 종류만 남긴다.
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError(safe_request_failure("OpenDART", endpoint, exc)) from None
        if binary:
            return payload
        if not isinstance(payload, dict):
            raise ProviderError(f"OpenDART {endpoint} returned invalid JSON")
        status = str(payload.get("status") or "")
        if status == "013":
            return {"list": []}
        if status != "000":
            raise ProviderError(f"OpenDART {endpoint} rejected the request ({status or 'unknown'})")
        return payload

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
            batch_rows = self._multi_account_batch(batch, year, quarter)
            rows.extend(batch_rows)
            self._progress(
                "open_dart_batch_done", endpoint="fnlttMultiAcnt.json",
                year=year, quarter=quarter, batch=batch_number,
                batch_count=batch_count, row_count=len(batch_rows),
            )
        return rows

    def _multi_account_batch(self, corp_codes: list[str], year: int, quarter: int) -> list[dict[str, Any]]:
        payload = self._get("fnlttMultiAcnt.json", {
            "corp_code": ",".join(corp_codes),
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter],
        })
        return [row for row in payload.get("list", []) if isinstance(row, dict)]

    def company_profile(self, corp_code: str) -> dict[str, str] | None:
        payload = self._get("company.json", {"corp_code": corp_code})
        industry_code = str(payload.get("induty_code") or "").strip()
        if not industry_code:
            return None
        return {"industry_code": industry_code}

    def single_accounts(
        self,
        corp_code: str,
        year: int,
        quarter: int,
        consolidation_scope: str,
    ) -> list[dict[str, Any]]:
        payload = self._get("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter],
            "fs_div": consolidation_scope,
        })
        return [row for row in payload.get("list", []) if isinstance(row, dict)]

    def periodic_filings(self, start: date, end: date) -> list[PeriodicFiling]:
        """접수번호로 중복을 구분할 수 있는 정기공시 목록을 반환한다."""
        result: dict[str, PeriodicFiling] = {}
        page = 1
        while True:
            payload = self._get("list.json", {
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "A",
                "page_no": str(page),
                "page_count": "100",
            })
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


class KisClient:
    """OpenDART에서 빠진 국내 분기 손익을 KIS 누적값으로 보충한다."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        *,
        cached_token: Callable[[], str | None] | None = None,
        save_token: Callable[[str, int], None] | None = None,
        session: Any | None = None,
        interval: float = 0.55,
    ) -> None:
        self.app_key = app_key.strip()
        self.app_secret = app_secret.strip()
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS credentials are required")
        self.cached_token = cached_token
        self.save_token = save_token
        self.session = session or _session()
        self.interval = interval
        self._last_request = 0.0
        self._token: str | None = None
        self.request_count = 0

    def _access_token(self) -> str:
        if self._token:
            return self._token
        cached = self.cached_token() if self.cached_token else None
        if cached:
            self._token = cached
            return cached
        try:
            payload = bounded_request(
                self.session, "POST",
                f"{KIS_BASE}/oauth2/tokenP",
                provider="KIS", operation="access-token",
                json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
                total_timeout=KIS_TOTAL_TIMEOUT,
                attempt_timeout=7,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=15,
            )
            token = str(payload.get("access_token") or "")
            expires = int(payload.get("expires_in") or 86400)
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            raise ProviderError(safe_request_failure("KIS", "access-token", exc)) from None
        if not token:
            raise ProviderError("KIS access-token response did not include a token")
        if self.save_token:
            self.save_token(token, expires)
        self._token = token
        return token

    def quarter_financials(self, ticker: str, year: int, quarter: int) -> dict[str, Decimal | None]:
        remaining = self.interval - (time.monotonic() - self._last_request)
        if self._last_request and remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()
        self.request_count += 1
        # KIS 정의: 0=연간, 1=분기. 분기 응답은 연초부터의 누적값이므로
        # 아래에서 직전 분기 누적값을 차감해 단독 분기값으로 변환한다.
        params = {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}
        headers = {
            "authorization": f"Bearer {self._access_token()}", "appkey": self.app_key,
            "appsecret": self.app_secret, "tr_id": "FHKST66430200", "custtype": "P",
        }
        try:
            payload = bounded_request(
                self.session, "GET",
                f"{KIS_BASE}/uapi/domestic-stock/v1/finance/income-statement",
                provider="KIS", operation=f"financials {ticker}",
                params=params, headers=headers,
                total_timeout=KIS_TOTAL_TIMEOUT,
                attempt_timeout=7,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=FAST_READ_TIMEOUT,
            )
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            raise ProviderError(safe_request_failure("KIS", f"financials {ticker}", exc)) from None
        if not isinstance(payload, dict):
            raise ProviderError(f"KIS financials {ticker} returned invalid JSON")
        result_code = str(payload.get("rt_cd") or "0")
        if result_code != "0":
            message_code = re.sub(r"[^0-9A-Za-z_-]", "", str(payload.get("msg_cd") or "unknown"))
            raise ProviderError(f"KIS financials {ticker} rejected the request ({message_code})")
        fields = {
            "top_line": "sale_account",
            "operating_income": "bsop_prti",
            "net_income": "thtr_ntin",
        }
        cumulative: dict[str, dict[int, Decimal]] = {field: {} for field in fields}
        for row in payload.get("output", []) if isinstance(payload, dict) else []:
            period = re.sub(r"\D", "", str(row.get("stac_yymm") or ""))
            if len(period) != 6 or int(period[:4]) != year:
                continue
            month = int(period[4:])
            if month in {3, 6, 9, 12}:
                for field, response_key in fields.items():
                    amount = _decimal(row.get(response_key))
                    if amount is not None:
                        cumulative[field][month // 3] = amount
        result: dict[str, Decimal | None] = {}
        for field, values in cumulative.items():
            current = values.get(quarter)
            previous = values.get(quarter - 1) if quarter > 1 else Decimal(0)
            result[field] = (
                (current - previous) * Decimal("100000000")
                if current is not None and previous is not None else None
            )
        return result


class EcosFxClient:
    """분기 말 이전 최근 USD/KRW 종가를 한 번만 조회해 재사용한다."""

    def __init__(self, api_key: str, *, session: Any | None = None) -> None:
        if not api_key.strip():
            raise ValueError("ECOS API key is required")
        self.api_key = api_key.strip()
        self.session = session or _session()
        self.cache: dict[date, Decimal] = {}
        self.request_count = 0

    def usd_krw(self, reference_date: date) -> Decimal:
        if reference_date in self.cache:
            return self.cache[reference_date]
        start = reference_date - timedelta(days=10)
        url = (
            f"{ECOS_BASE}/{self.api_key}/json/kr/1/100/731Y001/D/"
            f"{start:%Y%m%d}/{reference_date:%Y%m%d}/0000001"
        )
        self.request_count += 1
        try:
            payload = bounded_request(
                self.session, "GET", url,
                provider="ECOS", operation="USD/KRW",
                total_timeout=ECOS_TOTAL_TIMEOUT,
                attempt_timeout=9,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=15,
            )
        except ExecutionDeadlineExceeded:
            raise
        except Exception as exc:
            raise ProviderError(safe_request_failure("ECOS", "USD/KRW", exc)) from None
        if not isinstance(payload, dict):
            raise ProviderError("ECOS USD/KRW returned invalid JSON")
        result = payload.get("RESULT")
        if isinstance(result, dict):
            code = re.sub(r"[^0-9A-Za-z_-]", "", str(result.get("CODE") or "unknown"))
            raise ProviderError(f"ECOS USD/KRW rejected the request ({code})")
        statistic = payload.get("StatisticSearch")
        rows = statistic.get("row") if isinstance(statistic, dict) else None
        if not isinstance(rows, list):
            raise ProviderError("ECOS USD/KRW returned invalid data")
        candidates: list[tuple[str, Decimal]] = []
        for row in rows if isinstance(rows, list) else []:
            value = _decimal(row.get("DATA_VALUE"))
            observed = str(row.get("TIME") or "")
            if value is not None and value > 0 and observed <= reference_date.strftime("%Y%m%d"):
                candidates.append((observed, value))
        if not candidates:
            raise ProviderError("ECOS returned no USD/KRW value near quarter end")
        rate = max(candidates, key=lambda item: item[0])[1]
        self.cache[reference_date] = rate
        return rate

