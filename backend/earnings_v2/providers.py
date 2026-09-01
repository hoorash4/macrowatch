from __future__ import annotations

import io
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

from .models import Security


OPEN_DART_BASE = "https://opendart.fss.or.kr/api"
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
KIS_BASE = "https://openapi.koreainvestment.com:9443"
ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
KRX_ENDPOINTS = {"kr_largecap": "stk_bydd_trd", "kr_kosdaq": "ksq_bydd_trd"}


class ProviderError(RuntimeError):
    """API 키나 전체 요청 URL을 노출하지 않는 공급자 오류."""


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").replace(",", "").replace(" ", "").strip()
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
BINARY_TOTAL_TIMEOUT = 30


def _session() -> requests.Session:
    """자동 재시도 없이 한 번만 호출한다.

    분기 파이프라인은 같은 요청을 내부에서 여러 번 숨겨 실행하지 않는다.
    공급자 장애는 제한시간 안에 실패시키고 다음 실행에서 분기 전체를 다시
    검증한다. 그래야 부분 결과와 장시간 정지를 동시에 막을 수 있다.
    """
    # requests.Session 기본값은 자동 재시도 0회다. 별도 어댑터를 붙이지
    # 않아 공급자 호출 횟수가 코드에 보이는 횟수와 정확히 일치하게 한다.
    return requests.Session()


class OpenDartClient:
    """OpenDART 다중기업 주요계정 API만 담당한다."""

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

    def _get(self, endpoint: str, params: dict[str, str], *, binary: bool = False) -> Any:
        self._wait()
        self.request_count += 1
        try:
            response = self.session.get(
                f"{OPEN_DART_BASE}/{endpoint}",
                params={"crtfc_key": self.api_key, **params},
                timeout=(CONNECT_TIMEOUT, STANDARD_READ_TIMEOUT),
                stream=binary,
            )
            response.raise_for_status()
            if binary:
                # requests의 read timeout은 응답 전체 시간이 아니라 소켓에서
                # 다음 바이트를 기다리는 시간이다. 공급자가 데이터를 조금씩
                # 보내면 무한히 늘어질 수 있으므로 대용량 ZIP은 전체 시간을
                # 별도로 제한한다. 시간 초과 시 분기 전체가 실패한다.
                started = time.monotonic()
                chunks: list[bytes] = []
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if time.monotonic() - started > BINARY_TOTAL_TIMEOUT:
                        raise ProviderError(f"OpenDART {endpoint} response exceeded total deadline")
                    if chunk:
                        chunks.append(chunk)
                return b"".join(chunks)
            payload = response.json()
        except Exception:
            raise ProviderError(f"OpenDART {endpoint} request failed") from None
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
        for start in range(0, len(unique), 100):
            batch = unique[start:start + 100]
            payload = self._get("fnlttMultiAcnt.json", {
                "corp_code": ",".join(batch),
                "bsns_year": str(year),
                "reprt_code": REPORT_CODES[quarter],
            })
            rows.extend(row for row in payload.get("list", []) if isinstance(row, dict))
        return rows

    def recent_periodic_corp_codes(self, start: date, end: date) -> set[str]:
        """조회 구간에 새로 접수된 정기공시의 기업코드만 반환한다."""
        result: set[str] = set()
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
                report = str(row.get("report_nm") or "")
                if re.fullmatch(r"\d{8}", code) and any(name in report for name in ("분기보고서", "반기보고서", "사업보고서")):
                    result.add(code)
            total_pages = int(payload.get("total_page") or 1)
            if page >= total_pages:
                break
            page += 1
        return result


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
            response = self.session.get(
                f"{KRX_BASE}/{endpoint}",
                params={"basDd": day.strftime("%Y%m%d")},
                headers={"AUTH_KEY": self.auth_key, "Accept": "application/json"},
                timeout=(CONNECT_TIMEOUT, STANDARD_READ_TIMEOUT),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            raise ProviderError(f"KRX {endpoint} request failed") from None
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
    """OpenDART에서 매출만 빠진 국내 기업을 보충한다."""

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
            response = self.session.post(
                f"{KIS_BASE}/oauth2/tokenP",
                json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
                timeout=(CONNECT_TIMEOUT, 15),
            )
            response.raise_for_status()
            payload = response.json()
            token = str(payload.get("access_token") or "")
            expires = int(payload.get("expires_in") or 86400)
        except Exception:
            raise ProviderError("KIS access-token request failed") from None
        if not token:
            raise ProviderError("KIS access-token response did not include a token")
        if self.save_token:
            self.save_token(token, expires)
        self._token = token
        return token

    def quarter_top_line(self, ticker: str, year: int, quarter: int) -> Decimal | None:
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
            response = self.session.get(
                f"{KIS_BASE}/uapi/domestic-stock/v1/finance/income-statement",
                params=params, headers=headers, timeout=(CONNECT_TIMEOUT, FAST_READ_TIMEOUT),
            )
            payload = response.json()
            if not response.ok or str(payload.get("rt_cd") or "0") != "0":
                raise RuntimeError
        except Exception:
            raise ProviderError(f"KIS top-line request failed for {ticker}") from None
        cumulative: dict[int, Decimal] = {}
        for row in payload.get("output", []) if isinstance(payload, dict) else []:
            period = re.sub(r"\D", "", str(row.get("stac_yymm") or ""))
            amount = _decimal(row.get("sale_account"))
            if len(period) != 6 or int(period[:4]) != year or amount is None:
                continue
            month = int(period[4:])
            if month in {3, 6, 9, 12}:
                cumulative[month // 3] = amount
        current = cumulative.get(quarter)
        previous = cumulative.get(quarter - 1) if quarter > 1 else Decimal(0)
        if current is None or previous is None:
            return None
        # KIS 손익계산서 단위는 억원이다.
        return (current - previous) * Decimal("100000000")


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
            response = self.session.get(url, timeout=(CONNECT_TIMEOUT, 15))
            response.raise_for_status()
            payload = response.json()
            rows = payload["StatisticSearch"]["row"]
        except Exception:
            raise ProviderError("ECOS USD/KRW request failed") from None
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
