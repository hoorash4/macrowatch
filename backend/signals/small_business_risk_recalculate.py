"""Recalculate all stored U.S. small-business risk rows from stored components only."""
from __future__ import annotations

import json
from datetime import date

from common import SupabaseRest
from signals.small_business_risk_pipeline import build_rows, fetch_stored_delinquency


TABLE = "us_small_business_risk_monthly"


def recalculate(db: SupabaseRest | None = None) -> dict[str, object]:
    database = db or SupabaseRest()
    saved = database.request("GET", TABLE, params={
        "select": "month,sales_expectation_net,borrowing_difficulty_pct,optimism_index",
        "order": "month.asc",
        "limit": "10000",
    }) or []
    if not saved:
        raise RuntimeError("저장된 미국 중소기업 위험지수 원자료가 없습니다.")
    sales = {str(row["month"]): float(row["sales_expectation_net"]) for row in saved}
    borrowing = {str(row["month"]): float(row["borrowing_difficulty_pct"]) for row in saved}
    optimism = {
        str(row["month"]): float(row["optimism_index"])
        for row in saved if row.get("optimism_index") is not None
    }
    start, end = date.fromisoformat(str(saved[0]["month"])), date.fromisoformat(str(saved[-1]["month"]))
    delinquency = fetch_stored_delinquency(database, start, end)
    rows = build_rows(sales, borrowing, delinquency, optimism, date.today())
    calculated_months = {str(row["month"]) for row in rows}
    missing = [str(row["month"]) for row in saved if str(row["month"]) not in calculated_months]
    if missing:
        raise RuntimeError(f"연체율을 연결할 수 없는 월이 있어 저장을 중단합니다: {', '.join(missing[:12])}")
    database.upsert(TABLE, rows, conflict="month")
    result: dict[str, object] = {
        "rows": len(rows), "earliest": rows[0]["month"], "latest": rows[-1]["month"],
    }
    print(json.dumps({"mode": "stored-components-recalculation", **result}, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    recalculate()
