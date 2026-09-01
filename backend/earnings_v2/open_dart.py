from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import io
import os
import re
import time
from typing import Any
import zipfile
import xml.etree.ElementTree as ET

import requests

from .financials import StatementAmount, financial_top_line, single_quarter_amount


BASE = "https://opendart.fss.or.kr/api"
REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
OP_IDS = {"dart_operatingincomeloss", "ifrsfull_operatingprofitloss"}
NET_IDS = {"ifrsfull_profitloss", "dart_profitloss"}
REVENUE_IDS = {"ifrsfull_revenue", "dart_revenue", "ifrs_revenue"}


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").replace(",", "").strip()
    if text in ("", "-", "—"):
        return None
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def _metric_row(rows: list[dict[str, Any]], ids: set[str], names: tuple[str, ...]) -> dict[str, Any] | None:
    candidates = [row for row in rows if _norm(row.get("account_id")) in ids]
    if not candidates:
        candidates = [row for row in rows if any(name in _norm(row.get("account_nm")) for name in names)]
    return candidates[0] if candidates else None


def _amount(row: dict[str, Any] | None, quarter: int, prior_cumulative: Decimal | None = None) -> Decimal | None:
    if row is None:
        return None
    current = _decimal(row.get("thstrm_amount"))
    cumulative = _decimal(row.get("thstrm_add_amount"))
    if quarter == 4:
        cumulative = current if current is not None else cumulative
        current = None
    return single_quarter_amount(
        quarter, current_three_month=current, cumulative=cumulative,
        previous_cumulative=prior_cumulative,
    )


def _is_revenue_name(name: str) -> bool:
    positive = ("수익", "매출", "보험료수익", "이자수익", "수수료수익")
    negative = ("비용", "원가", "손실", "차감", "지출")
    return any(token in name for token in positive) and not any(token in name for token in negative)


class OpenDartV2Client:
    def __init__(self, api_key: str, *, interval: float = 0.15) -> None:
        if not api_key.strip():
            raise ValueError("OpenDART API key is required")
        self.api_key = api_key.strip()
        self.interval = interval
        self.session = requests.Session()
        self.last_request = 0.0

    @classmethod
    def from_env(cls) -> "OpenDartV2Client":
        key = os.getenv("OPENDART_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Missing OPENDART_API_KEY")
        return cls(key)

    def _get(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        elapsed = time.monotonic() - self.last_request
        if self.last_request and elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        response = self.session.get(
            f"{BASE}/{endpoint}", params={"crtfc_key": self.api_key, **params}, timeout=60,
        )
        self.last_request = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        status = str(payload.get("status") or "")
        if status == "013":
            return {"list": []}
        if status != "000":
            raise RuntimeError(f"OpenDART {status}: {str(payload.get('message') or '')[:200]}")
        return payload

    def corp_code_map(self) -> dict[str, tuple[str, str]]:
        response = self.session.get(f"{BASE}/corpCode.xml", params={"crtfc_key": self.api_key}, timeout=60)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            root = ET.fromstring(archive.read("CORPCODE.xml"))
        result = {}
        for node in root.findall("list"):
            stock_code = (node.findtext("stock_code") or "").strip()
            corp_code = (node.findtext("corp_code") or "").strip()
            corp_name = (node.findtext("corp_name") or "").strip()
            if re.fullmatch(r"\d{6}", stock_code) and re.fullmatch(r"\d{8}", corp_code):
                result[stock_code] = (corp_code, corp_name)
        return result

    def all_accounts(self, corp_code: str, year: int, quarter: int, scope: str) -> list[dict[str, Any]]:
        payload = self._get("fnlttSinglAcntAll.json", {
            "corp_code": corp_code, "bsns_year": str(year),
            "reprt_code": REPORT_CODES[quarter], "fs_div": scope,
        })
        return [row for row in payload.get("list", []) if isinstance(row, dict)]

    def quarter_values(self, corp_code: str, year: int, quarter: int) -> dict[str, Any] | None:
        for scope in ("CFS", "OFS"):
            rows = self.all_accounts(corp_code, year, quarter, scope)
            if not rows:
                continue
            prior_rows = self.all_accounts(corp_code, year, 3, scope) if quarter == 4 else []
            op_row = _metric_row(rows, OP_IDS, ("영업이익", "영업손익"))
            net_row = _metric_row(rows, NET_IDS, ("당기순이익", "분기순이익", "반기순이익"))
            revenue_row = _metric_row(rows, REVENUE_IDS, ("매출액", "영업수익"))
            prior_op = _metric_row(prior_rows, OP_IDS, ("영업이익", "영업손익"))
            prior_net = _metric_row(prior_rows, NET_IDS, ("당기순이익", "분기순이익", "반기순이익"))
            prior_rev = _metric_row(prior_rows, REVENUE_IDS, ("매출액", "영업수익"))
            op = _amount(op_row, quarter, _decimal(prior_op.get("thstrm_add_amount")) if prior_op else None)
            net = _amount(net_row, quarter, _decimal(prior_net.get("thstrm_add_amount")) if prior_net else None)
            top = _amount(revenue_row, quarter, _decimal(prior_rev.get("thstrm_add_amount")) if prior_rev else None)
            method = "reported_total"
            if top is None and op_row is not None:
                op_order = int(str(op_row.get("ord") or "999999").replace(",", ""))
                statement_rows = [row for row in rows if str(row.get("sj_div") or "") in ("IS", "CIS")]
                candidates = []
                prior_by_account = {
                    _norm(row.get("account_id")): row for row in prior_rows
                }
                for row in statement_rows:
                    try:
                        order = int(str(row.get("ord") or "999999").replace(",", ""))
                    except ValueError:
                        continue
                    name = str(row.get("account_nm") or "")
                    prior_row = prior_by_account.get(_norm(row.get("account_id")))
                    prior_amount = (
                        _decimal(prior_row.get("thstrm_add_amount"))
                        if prior_row is not None else None
                    )
                    amount = _amount(row, quarter, prior_amount)
                    if order < op_order and amount is not None and _is_revenue_name(name):
                        candidates.append(StatementAmount(name, amount, is_revenue=True))
                top, method = financial_top_line(candidates)
            if top is not None and op is not None and net is not None:
                representative = op_row or net_row or revenue_row or rows[0]
                return {
                    "top_line": top, "operating_income": op, "net_income": net,
                    "scope": scope, "top_line_method": method,
                    "source_filing_id": str(representative.get("rcept_no") or f"{corp_code}:{year}Q{quarter}"),
                    "currency": str(representative.get("currency") or "KRW").upper(),
                }
        return None
