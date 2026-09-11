"""Complete derived economic-chart spreads using paginated stored observations."""
from __future__ import annotations

from datetime import date, timedelta

from common import SupabaseRest

TABLE = "economic_chart_points"
PAGE = 1000


def read_values(db: SupabaseRest, code: str, start: date, end: date) -> dict[str, float]:
    out: dict[str, float] = {}
    offset = 0
    while True:
        rows = db.request("GET", TABLE, params={
            "select": "observation_date,value",
            "series_code": f"eq.{code}",
            "observation_date": f"gte.{start.isoformat()}",
            "and": f"(observation_date.lte.{end.isoformat()})",
            "order": "observation_date.asc",
            "limit": str(PAGE),
            "offset": str(offset),
        }) or []
        for row in rows:
            out[str(row["observation_date"])] = float(row["value"])
        if len(rows) < PAGE:
            break
        offset += PAGE
    return out


def existing_dates(db: SupabaseRest, code: str, start: date) -> set[str]:
    out: set[str] = set()
    offset = 0
    while True:
        rows = db.request("GET", TABLE, params={
            "select": "observation_date",
            "series_code": f"eq.{code}",
            "observation_date": f"gte.{start.isoformat()}",
            "order": "observation_date.asc",
            "limit": str(PAGE),
            "offset": str(offset),
        }) or []
        out.update(str(row["observation_date"]) for row in rows if row.get("observation_date"))
        if len(rows) < PAGE:
            break
        offset += PAGE
    return out


def main() -> None:
    today = date.today()
    start = today - timedelta(days=3660)
    db = SupabaseRest()
    left = read_values(db, "KR10Y", start, today)
    right = read_values(db, "KR3Y", start, today)
    existing = existing_dates(db, "KR10Y3Y", start)
    rows = [{
        "series_code": "KR10Y3Y",
        "observation_date": observed,
        "value": round(left[observed] - right[observed], 6),
        "frequency": "D",
        "source": "DERIVED:KR10Y-KR3Y",
    } for observed in sorted(left.keys() & right.keys()) if observed not in existing]
    if rows:
        db.upsert(TABLE, rows, conflict="series_code,observation_date")
    print({"series": "KR10Y3Y", "inserted": len(rows), "total_overlap": len(left.keys() & right.keys())})


if __name__ == "__main__":
    main()
