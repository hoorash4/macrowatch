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
        self._xbrl_cache: dict[str, bytes] = {}
        self._xbrl_cache_lock = Lock()

    @classmethod
    def from_env(cls) -> "OpenDartV2Client":
        key = os.getenv("OPENDART_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing OPENDART_API_KEY")
        return cls(key)

    def _wait_for_slot(self) -> None:
        elapsed = time.monotonic() - self.last_request
        if self.last_request and elapsed < self.interval:
            time.sleep(self.interval - elapsed)

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        self._wait_for_slot()
        try:
            response = get_with_retries(
                self.session,
                f"{BASE_URL}/{endpoint}",
                params={"crtfc_key": self.api_key, **params},
                timeout=(10, 30),
                attempts=3,
            )
            response.raise_for_status()
        except Exception:
            # requests may include the expanded URL (and API key) in errors.
            raise OpenDartV2Error(f"{endpoint} transport request failed") from None
        self.last_request = time.monotonic()
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
        self.last_request = time.monotonic()
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

    def _download_xbrl_archive(self, receipt_no: str, session: Any | None = None) -> bytes:
        """Download and validate one archive using an isolated HTTP session."""
        normalized = str(receipt_no or "").strip()
        if not re.fullmatch(r"\d{14}", normalized):
            raise ValueError("OpenDART XBRL download requires a 14-digit receipt number")
        try:
            response = get_with_retries(
                session if session is not None else resilient_session(),
                f"{BASE_URL}/fnlttXbrl.xml",
                params={"crtfc_key": self.api_key, "rcept_no": normalized},
                timeout=(10, 60),
                attempts=3,
            )
            response.raise_for_status()
        except Exception:
            raise OpenDartV2Error("fnlttXbrl.xml transport request failed") from None
        payload = bytes(response.content)
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                if not archive.namelist():
                    raise zipfile.BadZipFile("empty archive")
        except zipfile.BadZipFile:
            raise OpenDartV2Error("fnlttXbrl.xml returned an invalid XBRL archive") from None
        return payload

    def xbrl_archives(
        self,
        receipt_nos: Iterable[str],
        *,
        workers: int = 4,
    ) -> tuple[dict[str, bytes], dict[str, str]]:
        """Fetch unique missing filings concurrently and reuse them for this run."""
        receipts = list(dict.fromkeys(str(value or "").strip() for value in receipt_nos))
        results: dict[str, bytes] = {}
        errors: dict[str, str] = {}
        pending = []
        with self._xbrl_cache_lock:
            for receipt in receipts:
                cached = self._xbrl_cache.get(receipt)
                if cached is not None:
                    results[receipt] = cached
                else:
                    pending.append(receipt)
        if len(pending) == 1:
            receipt = pending[0]
            try:
                payload = self._download_xbrl_archive(receipt, self.session)
            except Exception as error:
                errors[receipt] = str(error)[:180]
            else:
                results[receipt] = payload
                with self._xbrl_cache_lock:
                    self._xbrl_cache[receipt] = payload
        elif pending:
            with ThreadPoolExecutor(max_workers=min(max(1, workers), len(pending))) as executor:
                futures = {
                    executor.submit(self._download_xbrl_archive, receipt): receipt
                    for receipt in pending
                }
                for future in as_completed(futures):
                    receipt = futures[future]
                    try:
                        payload = future.result()
                    except Exception as error:
                        errors[receipt] = str(error)[:180]
                        continue
                    results[receipt] = payload
                    with self._xbrl_cache_lock:
                        self._xbrl_cache[receipt] = payload
        return results, errors

    def xbrl_archive(self, receipt_no: str) -> bytes:
        """Download one filing's XBRL ZIP without writing it to disk."""
        normalized = str(receipt_no or "").strip()
        results, errors = self.xbrl_archives([normalized], workers=1)
        if normalized in results:
            return results[normalized]
        raise OpenDartV2Error(errors.get(normalized, "XBRL archive unavailable"))
