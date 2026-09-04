"""Read-only probe for the Financial Services Commission detailed income-statement API."""

from __future__ import annotations

import json
import os
import re
from urllib.parse import unquote

from .http import bounded_request, provider_session, safe_request_failure
from .providers import OpenDartClient

FINANCIAL_COMPANY_FUNCTION = "earnings-financial-company-source"
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
    accounts = payload.get("accounts")
    return [row for row in accounts if isinstance(row, dict)] if isinstance(accounts, list) else []


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
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    internal_token = os.getenv("EARNINGS_FINANCIAL_SOURCE_TOKEN", "").strip()
    if not all((public_key, supabase_url, service_key, internal_token)):
        raise SystemExit("Missing financial source proxy credentials")

    corp_map = dart.corporation_map()
    session = provider_session()
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
        try:
            payload = bounded_request(
                session,
                "POST",
                f"{supabase_url}/functions/v1/{FINANCIAL_COMPANY_FUNCTION}",
                provider="Financial Services Commission",
                operation="income-statement-proxy",
                headers={
                    "Authorization": f"Bearer {internal_token}",
                    "apikey": service_key,
                    "Content-Type": "application/json",
                    "X-Public-Data-API-Key": public_key,
                },
                json={"crno": crno, "fiscal_year": 2018, "mode": "income_statement"},
                total_timeout=90,
                attempt_timeout=30,
                connect_timeout=5,
                read_timeout=30,
            )        except Exception as error:
            print(json.dumps({
                "stage": "financial_income_statement_probe",
                "company": name,
                "stock_code": stock_code,
                "fiscal_year": 2018,
                "status": "transport_error",
                "error": safe_request_failure("Financial Services Commission", "income-statement-proxy", error),
            }, ensure_ascii=False), flush=True)
            continue
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
            "fiscal_year": 2018,
            "status": "source_error" if _source_error(payload) else "ok",
            "source_error": _source_error(payload),
            "row_count": len(rows),
            "matching_accounts": matched,
        }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
