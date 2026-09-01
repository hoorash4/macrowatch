from __future__ import annotations

import io
import os
import re
import time
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

    def all_accounts(self, corp_code: str, year: int, quarter: int, scope: str) -> list[dict[str, Any]]:
        normalized_scope = scope.upper()
        if normalized_scope not in {"CFS", "OFS"}:
            raise ValueError("OpenDART scope must be CFS or OFS")
        payload = self._get_json("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter],
            "fs_div": normalized_scope,
        })
        return [row for row in payload.get("list", []) if isinstance(row, dict)]
