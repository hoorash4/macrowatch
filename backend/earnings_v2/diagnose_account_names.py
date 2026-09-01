from __future__ import annotations

import json
import os
import re

from .open_dart import OpenDartV2Client


def main() -> int:
    codes = list(dict.fromkeys(
        code.strip()
        for code in os.environ["DART_DIAGNOSE_CORP_CODES"].split(",")
        if code.strip()
    ))
    year = int(os.environ["DART_DIAGNOSE_YEAR"])
    quarter = int(os.environ["DART_DIAGNOSE_QUARTER"])
    scope = os.getenv("DART_DIAGNOSE_SCOPE", "CFS").strip().upper()
    if not codes or any(not re.fullmatch(r"\d{8}", code) for code in codes):
        raise ValueError("corporation codes must be comma-separated 8-digit values")
    if quarter not in (1, 2, 3, 4):
        raise ValueError("quarter must be between 1 and 4")
    if scope not in {"CFS", "OFS"}:
        raise ValueError("scope must be CFS or OFS")
    client = OpenDartV2Client.from_env()
    requests = [(code, year, quarter, scope) for code in codes]
    results, errors = client.all_accounts_many(requests, workers=4)

    for request in requests:
        rows = [
            {
                "account_id": str(row.get("account_id") or ""),
                "account_name": str(row.get("account_nm") or ""),
                "statement": str(row.get("sj_div") or ""),
                "order": str(row.get("ord") or ""),
                "current_amount": str(row.get("thstrm_amount") or ""),
                "current_cumulative": str(row.get("thstrm_add_amount") or ""),
            }
            for row in results.get(request, [])
            if str(row.get("sj_div") or "").upper() in {"IS", "CIS"}
            and any(token in str(row.get("account_nm") or "") for token in ("매출", "수익", "영업"))
        ]
        print(json.dumps({
            "corp_code": request[0],
            "year": year,
            "quarter": quarter,
            "scope": scope,
            "error": errors.get(request),
            "accounts": rows,
        }, ensure_ascii=False), flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
