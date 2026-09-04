"""Read-only probe for the Financial Services Commission detailed income-statement API."""

from __future__ import annotations

import json
import os
import re
from urllib.parse import unquote

import requests

from .providers import OpenDartClient

FINANCIAL_INCOME_STATEMENT_URL = (
    "https://apis.data.go.kr/1160100/service/"
    "GetFnCoFinaStatCredInfoService_V2/getFnCoIs_V2"
)
SAMPLES = {
    "032830": "삼성생명",
    "105560": "KB금융",
    "055550": "신한지주",
    "000810": "삼성화재",
    "029780": "삼성카드",
    "071050": "한국금융지주",
}
ACCOUNT_PATTERN = re.compile(r"매출|수익|영업|손익|순이익|당기", re.IGNORECASE)


def _items(payload: dict[str, object]) -> list[dict[str, object]]:
    body = payload.get("FnCoIs_body")
    if not isinstance(body, dict):
        response = payload.get("response")
        body = response.get("body") if isinstance(response, dict) else None
    items = body.get("items") if isinstance(body, dict) else None
    item = items.get("item") if isinstance(items, dict) else None
    if isinstance(item, list):
        return [row for row in item if isinstance(row, dict)]
    return [item] if isinstance(item, dict) else []


def _source_error(payload: dict[str, object]) -> str | None:
    header = payload.get("FnCoIs_header")
    if not isinstance(header, dict):
        response = payload.get("response")
        header = response.get("header") if isinstance(response, dict) else None
    code = str(header.get("resultCode") or "") if isinstance(header, dict) else ""
    return None if code in {"", "00", "000"} else code


def main() -> None:
    dart = OpenDartClient.from_env()
    public_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not public_key:
        raise SystemExit("Missing DATA_GO_KR_SERVICE_KEY")

    corp_map = dart.corporation_map()
    for stock_code, name in SAMPLES.items():
        mapping = corp_map.get(stock_code)
        if mapping is None:
            print(json.dumps({"stage": "financial_income_statement_probe", "company": name, "status": "no_corp_code"}, ensure_ascii=False), flush=True)
            continue
        profile = dart.company_profile(mapping[0]) or {}
        crno = str(profile.get("jurir_no") or "")
        if not re.fullmatch(r"\d{13}", crno):
            print(json.dumps({"stage": "financial_income_statement_probe", "company": name, "status": "no_crno"}, ensure_ascii=False), flush=True)
            continue
        for fiscal_year in (2018, 2019):
            response = requests.get(
                FINANCIAL_INCOME_STATEMENT_URL,
                params={
                    "serviceKey": unquote(public_key),
                    "resultType": "json",
                    "pageNo": "1",
                    "numOfRows": "9999",
                    "crno": crno,
                    "bizYear": str(fiscal_year),
                },
                timeout=(5, 30),
            )
            response.raise_for_status()
            payload = response.json()
            rows = _items(payload)
            matched = [
                {
                    "basDt": row.get("basDt"),
                    "fnclDcd": row.get("fnclDcd"),
                    "fnclDcdNm": row.get("fnclDcdNm"),
                    "acitId": row.get("acitId"),
                    "acitNm": row.get("acitNm"),
                    "thqrAcitAmt": row.get("thqrAcitAmt"),
                    "crtmAcitAmt": row.get("crtmAcitAmt"),
                    "lsqtAcitAmt": row.get("lsqtAcitAmt"),
                    "curCd": row.get("curCd"),
                }
                for row in rows
                if ACCOUNT_PATTERN.search(str(row.get("acitNm") or ""))
            ]
            print(json.dumps({
                "stage": "financial_income_statement_probe",
                "company": name,
                "stock_code": stock_code,
                "fiscal_year": fiscal_year,
                "status": "source_error" if _source_error(payload) else "ok",
                "source_error": _source_error(payload),
                "row_count": len(rows),
                "matching_accounts": matched,
            }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
