"""Read-only audit of stored FOMC rate fields used by the chart projection."""
from __future__ import annotations

import json

from common import SupabaseRest


def audit(db: SupabaseRest | None = None) -> dict[str, object]:
    database = db or SupabaseRest()
    rows = database.request("GET", "central_bank_policy_events", params={
        "select": "meeting_date,action,change_bps,target_range_lower,target_range_upper",
        "central_bank": "eq.fed",
        "analysis_status": "eq.completed",
        "meeting_date": "gte.2009-01-01",
        "order": "meeting_date.asc",
        "limit": "10000",
    }) or []

    categories = {"complete": [], "upper_only": [], "lower_only": [], "neither": []}
    missing_nonhold_change: list[str] = []
    widths: dict[str, int] = {}
    for row in rows:
        observed = str(row.get("meeting_date") or "")
        lower, upper = row.get("target_range_lower"), row.get("target_range_upper")
        if lower is not None and upper is not None:
            category = "complete"
            width = str(round(float(upper) - float(lower), 4))
            widths[width] = widths.get(width, 0) + 1
        elif upper is not None:
            category = "upper_only"
        elif lower is not None:
            category = "lower_only"
        else:
            category = "neither"
        categories[category].append({
            "date": observed,
            "action": row.get("action"),
            "change_bps": row.get("change_bps"),
            "lower": lower,
            "upper": upper,
        })
        if row.get("action") != "hold" and row.get("change_bps") is None:
            missing_nonhold_change.append(observed)

    result: dict[str, object] = {
        "total": len(rows),
        "counts": {key: len(value) for key, value in categories.items()},
        "complete_widths": widths,
        "first_complete": categories["complete"][0] if categories["complete"] else None,
        "last_complete": categories["complete"][-1] if categories["complete"] else None,
        "upper_only_sample": categories["upper_only"][:10],
        "lower_only_sample": categories["lower_only"][:10],
        "neither_sample": categories["neither"][:10],
        "missing_nonhold_change_dates": missing_nonhold_change[:20],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    audit()
