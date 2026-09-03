from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

from .providers import OpenDartClient, ProviderError
from .repository import EarningsV2Repository
from .transform import extract_company_fact, normalize_label


SUPPORTED_YEARS = range(2016, 2019)
INCOME_STATEMENTS = {"IS", "CIS"}
RELEVANT_TOKENS = (
    "매출", "수익", "영업이익", "영업손익", "영업손실",
    "순이익", "순손익", "순손실", "profit", "revenue",
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Inspect historical OpenDART structured account names without DB writes",
    )
    result.add_argument("--year", type=int, required=True)
    result.add_argument("--quarter", type=int, choices=(1, 2, 3, 4), required=True)
    return result


def relevant_accounts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        statement = str(row.get("sj_div") or "").strip().upper()
        if statement not in INCOME_STATEMENTS:
            continue
        account_id = str(row.get("account_id") or "").strip()
        account_name = str(row.get("account_nm") or "").strip()
        searchable = f"{normalize_label(account_id)} {normalize_label(account_name)}"
        if not any(token in searchable for token in RELEVANT_TOKENS):
            continue
        key = (statement, account_id, account_name)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "sj_div": statement,
            "account_id": account_id,
            "account_nm": account_name,
            "thstrm_amount": row.get("thstrm_amount"),
            "thstrm_add_amount": row.get("thstrm_add_amount"),
        })
    return result


def main() -> None:
    args = parser().parse_args()
    if args.year not in SUPPORTED_YEARS:
        raise SystemExit("V2.5 diagnostics only allow fiscal years 2016 through 2018")

    repository = EarningsV2Repository.from_env()
    dart = OpenDartClient.from_env()
    pending = [
        row for row in repository.pending_rows()
        if int(row.get("market_year") or 0) == args.year
        and int(row.get("market_quarter") or 0) == args.quarter
    ]
    corporation_map = dart.corporation_map()
    results: list[dict[str, Any]] = []

    for row in pending:
        stock_code = str(row.get("stock_code") or "").strip()
        mapped = corporation_map.get(stock_code)
        company = str(row.get("company_name") or row.get("company_id") or stock_code)
        if mapped is None:
            results.append({
                "company": company,
                "stock_code": stock_code,
                "error": "corp_code_not_found",
            })
            continue
        corp_code, _official_name = mapped
        scopes: list[dict[str, Any]] = []
        combined_rows: list[dict[str, Any]] = []
        for scope in ("CFS", "OFS"):
            try:
                status, rows = dart.diagnose_single_accounts(
                    corp_code, args.year, args.quarter, scope,
                )
            except ProviderError as error:
                scopes.append({"scope": scope, "error": str(error)})
                continue
            combined_rows.extend(rows)
            statement_counts = Counter(
                str(item.get("sj_div") or "missing").strip().upper()
                for item in rows
            )
            scopes.append({
                "scope": scope,
                "status": status,
                "row_count": len(rows),
                "statement_counts": dict(sorted(statement_counts.items())),
                "relevant_accounts": relevant_accounts(rows),
            })

        fact = extract_company_fact(
            corp_code=corp_code,
            company_id=str(row.get("company_id") or stock_code),
            year=args.year,
            quarter=args.quarter,
            current_rows=combined_rows,
        )
        results.append({
            "company": company,
            "stock_code": stock_code,
            "corp_code": corp_code,
            "missing": {
                "top_line": bool(row.get("missing_top_line")),
                "operating_income": bool(row.get("missing_operating_income")),
                "net_income": bool(row.get("missing_net_income")),
            },
            "scopes": scopes,
            "recognized": None if fact is None else {
                "scope": fact.consolidation_scope,
                "top_line": fact.top_line,
                "operating_income": fact.operating_income,
                "net_income": fact.net_income,
            },
        })

    print(json.dumps({
        "period": f"{args.year}Q{args.quarter}",
        "pending_company_count": len(pending),
        "open_dart_request_count": dart.request_count,
        "companies": results,
    }, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
