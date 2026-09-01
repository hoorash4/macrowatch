from __future__ import annotations

import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any, Iterable
import zipfile
import xml.etree.ElementTree as ET

from .http import get_with_retries, resilient_session


BASE_URL = "https://opendart.fss.or.kr/api"
REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
MAX_MULTI_COMPANIES = 100


class OpenDartV2Error(RuntimeError):
    """Credential-safe provider or transport failure."""


class OpenDartV2Client:
    """OpenDART transport only; statement interpretation belongs to the parser."""

    def __init__(self, api_key: str, *, interval: float = 0.15, session: Any | None = None) -> None:
        if not api_key.strip():
            raise ValueError("OpenDART API key is required")
        self.api_key = api_key.strip()
        self.interval = interval
        self.session = session if session is not None else resilient_session()
        self.last_request = 0.0
        self._request_lock = Lock()
        self._account_cache: dict[tuple[str, int, int, str], list[dict[str, Any]]] = {}
        self._account_cache_lock = Lock()

    @classmethod
    def from_env(cls) -> "OpenDartV2Client":
        key = os.getenv("OPENDART_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing OPENDART_API_KEY")
        return cls(key)

    def _wait_for_slot(self) -> None:
        """Reserve one provider request slot across all worker threads."""
        with self._request_lock:
            elapsed = time.monotonic() - self.last_request
            if self.last_request and elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self.last_request = time.monotonic()

    def _get_json(
        self,
        endpoint: str,
        params: dict[str, str],
        *,
        session: Any | None = None,
    ) -> dict[str, Any]:
        self._wait_for_slot()
        try:
            response = get_with_retries(
                session if session is not None else self.session,
                f"{BASE_URL}/{endpoint}",
                params={"crtfc_key": self.api_key, **params},
                timeout=(10, 30),
                attempts=3,
            )
            response.raise_for_status()
        except Exception:
            # requests may include the expanded URL (and API key) in errors.
            raise OpenDartV2Error(f"{endpoint} transport request failed") from None
        try:
            payload = response.json()
        except Exception:
            raise OpenDartV2Error(f"{endpoint} returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise OpenDartV2Error(f"{endpoint} returned a non-object response")
        status = str(payload.get("status") or "")
        if status == "013":
            return {"list": []}
        if status != "000":
            message = str(payload.get("message") or "").replace(self.api_key, "[redacted]")
            raise OpenDartV2Error(f"OpenDART {status or 'unknown'}: {message[:200]}")
        return payload

    def corp_code_map(self) -> dict[str, tuple[str, str]]:
        self._wait_for_slot()
        try:
            response = get_with_retries(
                self.session,
                f"{BASE_URL}/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=(10, 30),
                attempts=3,
            )
            response.raise_for_status()
        except Exception:
            raise OpenDartV2Error("corpCode.xml transport request failed") from None
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                root = ET.fromstring(archive.read("CORPCODE.xml"))
        except Exception:
            raise OpenDartV2Error("OpenDART corp-code archive is invalid") from None
        result = {}
        for node in root.findall("list"):
            stock_code = (node.findtext("stock_code") or "").strip()
            corp_code = (node.findtext("corp_code") or "").strip()
            corp_name = (node.findtext("corp_name") or "").strip()
            if re.fullmatch(r"\d{6}", stock_code) and re.fullmatch(r"\d{8}", corp_code):
                result[stock_code] = (corp_code, corp_name)
        return result

    def multi_accounts(self, corp_codes: Iterable[str], year: int, quarter: int) -> list[dict[str, Any]]:
        companies = list(dict.fromkeys(str(code).strip() for code in corp_codes if str(code).strip()))
        if not companies:
            return []
        if len(companies) > MAX_MULTI_COMPANIES:
            raise ValueError("OpenDART multi-company requests allow at most 100 companies")
        payload = self._get_json("fnlttMultiAcnt.json", {
            "corp_code": ",".join(companies),
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter],
        })
        return [row for row in payload.get("list", []) if isinstance(row, dict)]

    def _fetch_all_accounts(
        self,
        key: tuple[str, int, int, str],
        *,
        session: Any | None = None,
    ) -> list[dict[str, Any]]:
        corp_code, year, quarter, scope = key
        payload = self._get_json("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter],
            "fs_div": scope,
        }, session=session)
        return [row for row in payload.get("list", []) if isinstance(row, dict)]

    @staticmethod
    def _account_key(
        corp_code: str,
        year: int,
        quarter: int,
        scope: str,
    ) -> tuple[str, int, int, str]:
        code = str(corp_code or "").strip()
        normalized_scope = str(scope or "").upper()
        if not re.fullmatch(r"\d{8}", code):
            raise ValueError("OpenDART full-account request requires an 8-digit corporation code")
        if quarter not in REPORT_CODES:
            raise ValueError("quarter must be between 1 and 4")
        if normalized_scope not in {"CFS", "OFS"}:
            raise ValueError("scope must be CFS or OFS")
        return code, int(year), int(quarter), normalized_scope

    def all_accounts(
        self,
        corp_code: str,
        year: int,
        quarter: int,
        scope: str,
    ) -> list[dict[str, Any]]:
        key = self._account_key(corp_code, year, quarter, scope)
        with self._account_cache_lock:
            cached = self._account_cache.get(key)
        if cached is not None:
            return cached
        rows = self._fetch_all_accounts(key)
        with self._account_cache_lock:
            self._account_cache[key] = rows
        return rows

    def all_accounts_many(
        self,
        requests: Iterable[tuple[str, int, int, str]],
        *,
        workers: int = 4,
    ) -> tuple[
        dict[tuple[str, int, int, str], list[dict[str, Any]]],
        dict[tuple[str, int, int, str], str],
    ]:
        """Fetch only unique unresolved company-periods with bounded concurrency."""
        keys = list(dict.fromkeys(self._account_key(*request) for request in requests))
        results: dict[tuple[str, int, int, str], list[dict[str, Any]]] = {}
        errors: dict[tuple[str, int, int, str], str] = {}
        pending = []
        with self._account_cache_lock:
            for key in keys:
                cached = self._account_cache.get(key)
                if cached is None:
                    pending.append(key)
                else:
                    results[key] = cached

        def fetch(key: tuple[str, int, int, str]) -> list[dict[str, Any]]:
            session = resilient_session()
            try:
                return self._fetch_all_accounts(key, session=session)
            finally:
                session.close()

        if pending:
            with ThreadPoolExecutor(max_workers=min(max(1, workers), len(pending))) as executor:
                futures = {executor.submit(fetch, key): key for key in pending}
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        rows = future.result()
                    except Exception as error:
                        errors[key] = str(error)[:180]
                        continue
                    results[key] = rows
                    with self._account_cache_lock:
                        self._account_cache[key] = rows
        return results, errors
