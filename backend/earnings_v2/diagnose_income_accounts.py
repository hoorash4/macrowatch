from __future__ import annotations

import json
import os

from .open_dart import OpenDartV2Client


def _amount(row: dict) -> str:
    return str(row.get("thstrm_amount") or row.get("thstrm_add_amount") or "")


def main() -> int:
    corp_code = os.environ["DART_DIAGNOSE_CORP_CODE"].strip()
    year = int(os.environ["DART_DIAGNOSE_YEAR"])
    quarter = int(os.environ["DART_DIAGNOSE_QUARTER"])
    scope = os.getenv("DART_DIAGNOSE_SCOPE", "CFS").strip().upper()

    rows = OpenDartV2Client.from_env().all_accounts(corp_code, year, quarter, scope)
    income_rows = [
        row for row in rows
        if str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
    ]
    income_rows.sort(key=lambda row: int(str(row.get("ord") or "999999")))

    selected: list[dict[str, str]] = []
    for row in income_rows:
        account_name = str(row.get("account_nm") or "").strip()
        selected.append({
            "order": str(row.get("ord") or ""),
            "statement": str(row.get("sj_div") or ""),
            "account_id": str(row.get("account_id") or ""),
            "account_name": account_name,
            "amount": _amount(row),
            "currency": str(row.get("currency") or ""),
        })
        normalized = "".join(account_name.split()).replace("(손실)", "")
        if normalized in {"영업이익", "영업손익"}:
            break

    print(json.dumps({
        "corp_code": corp_code,
        "year": year,
        "quarter": quarter,
        "scope": scope,
        "accounts_through_operating_income": selected,
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
