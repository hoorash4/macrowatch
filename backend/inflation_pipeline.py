"""Publish the U.S. inflation composite from canonical official rates and Cleveland nowcasts."""

from __future__ import annotations

import json
import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone

import requests

try:
    from .common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, month_start_months_ago, request_with_retry
except ImportError:
    from common import AUTOMATIC_MONTHLY_PERIODS, SupabaseRest, month_start_months_ago, request_with_retry


TIMEOUT_SECONDS = 60
PUBLISH_START = date(2020, 1, 1)
CLEVELAND_YEARLY_URL = (
    "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/"
    "nowcast_year.json?sc_lang=en"
)
MODEL_VERSION = "cleveland_nowcast_v1"
OFFICIAL_INFLATION_SERIES = {
    "cpi": "US_CPI", "core_cpi": "US_CORE_CPI", "pce": "US_PCE",
    "core_pce": "US_CORE_PCE", "ppi": "US_PPI", "core_ppi": "US_CORE_PPI",
}


@dataclass(frozen=True)
class NowcastPoint:
    observed_on: date
    cpi_yoy_pct: float
    pce_yoy_pct: float


def parse_chart_date(label: str, target: date) -> date | None:
    try:
        month, day = (int(part) for part in label.split("/"))
        return date(target.year + (1 if month < target.month else 0), month, day)
    except (AttributeError, TypeError, ValueError):
        return None


def fetch_cleveland_nowcasts() -> dict[str, dict[date, list[NowcastPoint]]]:
    """Read Cleveland Fed's published year-over-year CPI and PCE nowcasts."""
    response = request_with_retry(lambda: requests.get(
        CLEVELAND_YEARLY_URL,
        headers={"User-Agent": "MacroWatch inflation dashboard/3.0"},
        timeout=TIMEOUT_SECONDS,
    ))
    response.raise_for_status()
    payload = response.json()
    output: dict[str, dict[date, list[NowcastPoint]]] = {"headline": {}, "core": {}}
    names = {"headline": ("CPI Inflation", "PCE Inflation"),
             "core": ("Core CPI Inflation", "Core PCE Inflation")}
    for item in payload if isinstance(payload, list) else []:
        caption = str((item.get("chart") or {}).get("subcaption") or "")
        try:
            target = datetime.strptime(caption[:10], "%Y-%m-%d").date().replace(day=1)
        except ValueError:
            try:
                target = datetime.strptime(caption[:7], "%Y-%m").date().replace(day=1)
            except ValueError:
                continue
        labels = ((item.get("categories") or [{}])[0].get("category") or [])
        datasets = {entry.get("seriesname"): entry.get("data", []) for entry in item.get("dataset", [])}
        for kind, (cpi_name, pce_name) in names.items():
            points: list[NowcastPoint] = []
            cpi_values, pce_values = datasets.get(cpi_name, []), datasets.get(pce_name, [])
            for index, label in enumerate(labels):
                observed_on = parse_chart_date(str(label.get("label") or ""), target)
                if observed_on is None:
                    continue
                try:
                    cpi = float(cpi_values[index]["value"])
                    pce = float(pce_values[index]["value"])
                except (IndexError, KeyError, TypeError, ValueError):
                    continue
                points.append(NowcastPoint(observed_on, cpi, pce))
            if points:
                output[kind][target] = sorted(points, key=lambda point: point.observed_on)
    if not output["headline"] or not output["core"]:
        raise RuntimeError("Cleveland Fed year-over-year nowcast history was empty or malformed")
    return output


def store_cleveland_nowcasts(db: SupabaseRest,
                             nowcasts: dict[str, dict[date, list[NowcastPoint]]]) -> int:
    recent_months = set(sorted({month for months in nowcasts.values() for month in months})[
        -AUTOMATIC_MONTHLY_PERIODS:
    ])
    rows = [{"kind": kind, "target_month": month.isoformat(),
             "observed_on": point.observed_on.isoformat(),
             "cpi_yoy_pct": round(point.cpi_yoy_pct, 8),
             "pce_yoy_pct": round(point.pce_yoy_pct, 8),
             "source": "CLEVELAND_FED:inflation_nowcasting"}
            for kind, months in nowcasts.items() for month, points in months.items()
            if month in recent_months for point in points]
    if rows:
        db.upsert("inflation_nowcast_vintages", rows,
                  conflict="kind,target_month,observed_on")
    return len(rows)


def load_cleveland_nowcasts(db: SupabaseRest, start_month: date) -> dict[str, dict[date, list[NowcastPoint]]]:
    output: dict[str, dict[date, list[NowcastPoint]]] = {"headline": {}, "core": {}}
    offset = 0
    while True:
        page = db.request("GET", "inflation_nowcast_vintages", params={
            "select": "kind,target_month,observed_on,cpi_yoy_pct,pce_yoy_pct",
            "target_month": f"gte.{start_month.isoformat()}",
            "order": "target_month.asc,observed_on.asc", "offset": str(offset), "limit": "1000",
        }) or []
        for row in page:
            kind = str(row["kind"])
            month = date.fromisoformat(str(row["target_month"])[:10])
            output[kind].setdefault(month, []).append(NowcastPoint(
                date.fromisoformat(str(row["observed_on"])[:10]),
                float(row["cpi_yoy_pct"]), float(row["pce_yoy_pct"]),
            ))
        if len(page) < 1000:
            break
        offset += len(page)
    if not output["headline"] or not output["core"]:
        raise RuntimeError("Stored Cleveland Fed nowcasts are empty")
    return output


def load_canonical_series(db: SupabaseRest, codes: tuple[str, ...]) -> dict[str, dict[date, float]]:
    values: dict[str, dict[date, float]] = {}
    for code in codes:
        rows = db.request("GET", "economic_chart_series_points", params={
            "select": "observation_date,value", "series_code": f"eq.{code}",
            "observation_date": f"gte.{PUBLISH_START.isoformat()}",
            "order": "observation_date.asc", "limit": "10000",
        }) or []
        values[code] = {date.fromisoformat(str(row["observation_date"])[:10]): float(row["value"])
                        for row in rows if row.get("value") is not None}
        if not values[code]:
            raise RuntimeError(f"Canonical inflation series is empty: {code}")
    return values


def latest_on_or_before(values: dict[date, float], target: date) -> float | None:
    available = [observed for observed in values if observed <= target]
    return values[max(available)] if available else None


def weighted_composite(pce: float, cpi: float, ppi: float) -> float:
    return 0.60 * pce + 0.30 * cpi + 0.10 * ppi


def fisher_real_rate_pct(policy: float, inflation: float) -> float:
    return 100.0 * ((1.0 + policy / 100.0) / (1.0 + inflation / 100.0) - 1.0)


def build_rows(official: dict[str, dict[date, float]],
               nowcasts: dict[str, dict[date, list[NowcastPoint]]],
               policy: dict[date, float]) -> list[dict[str, object]]:
    updated_at = datetime.now(timezone.utc).isoformat()
    official_months = sorted(official["US_CPI"].keys() & official["US_CORE_CPI"].keys()
                             & official["US_PCE"].keys() & official["US_CORE_PCE"].keys()
                             & official["US_PPI"].keys() & official["US_CORE_PPI"].keys())
    rows: list[dict[str, object]] = []

    def output(month: date, headline: float, core: float, status: str, data_as_of: date) -> dict[str, object]:
        policy_value = latest_on_or_before(policy, data_as_of)
        return {
            "month": month.isoformat(), "headline_yoy_pct": round(headline, 4),
            "core_yoy_pct": round(core, 4),
            "policy_rate_upper_pct": round(policy_value, 4) if policy_value is not None else None,
            "headline_real_rate_pct": round(fisher_real_rate_pct(policy_value, headline), 4) if policy_value is not None else None,
            "core_real_rate_pct": round(fisher_real_rate_pct(policy_value, core), 4) if policy_value is not None else None,
            "status": status, "model_version": MODEL_VERSION,
            "data_as_of": data_as_of.isoformat(), "updated_at": updated_at,
        }

    for month in official_months:
        rows.append(output(
            month,
            weighted_composite(official["US_PCE"][month], official["US_CPI"][month], official["US_PPI"][month]),
            weighted_composite(official["US_CORE_PCE"][month], official["US_CORE_CPI"][month], official["US_CORE_PPI"][month]),
            "final", month,
        ))
    final_months = set(official_months)
    for month in sorted(nowcasts["headline"].keys() & nowcasts["core"].keys()):
        if month in final_months:
            continue
        headline_point, core_point = nowcasts["headline"][month][-1], nowcasts["core"][month][-1]
        ppi = latest_on_or_before(official["US_PPI"], month)
        core_ppi = latest_on_or_before(official["US_CORE_PPI"], month)
        if ppi is None or core_ppi is None:
            continue
        rows.append(output(
            month,
            weighted_composite(headline_point.pce_yoy_pct, headline_point.cpi_yoy_pct, ppi),
            weighted_composite(core_point.pce_yoy_pct, core_point.cpi_yoy_pct, core_ppi),
            "provisional", max(headline_point.observed_on, core_point.observed_on),
        ))
    return sorted(rows, key=lambda row: str(row["month"]))


def save_automatic(db: SupabaseRest, rows: list[dict[str, object]]) -> int:
    recent = rows[-AUTOMATIC_MONTHLY_PERIODS:]
    existing = db.request("GET", "us_inflation_monthly", params={
        "select": "month,status,model_version", "order": "month.desc", "limit": "24",
    }) or []
    state = {str(row["month"]): row for row in existing}
    writable = [row for row in recent if str(row["month"]) not in state
                or state[str(row["month"])].get("status") == "provisional"
                or state[str(row["month"])].get("model_version") != MODEL_VERSION]
    if writable:
        db.upsert("us_inflation_monthly", writable, conflict="month")
    return len(writable)


def collect_sources() -> None:
    db = SupabaseRest(timeout=TIMEOUT_SECONDS)
    stored = store_cleveland_nowcasts(db, fetch_cleveland_nowcasts())
    if not stored:
        raise RuntimeError("Cleveland Fed source collection produced no rows")
    print(json.dumps({"mode": "automatic", "stage": "sources", "stored": stored}, ensure_ascii=False))


def run_automatic() -> None:
    db = SupabaseRest(timeout=TIMEOUT_SECONDS)
    official = load_canonical_series(db, tuple(OFFICIAL_INFLATION_SERIES.values()))
    policy = load_canonical_series(db, ("US_POLICY_RATE_MID",))["US_POLICY_RATE_MID"]
    nowcast_start = month_start_months_ago(date.today(), AUTOMATIC_MONTHLY_PERIODS - 1)
    rows = build_rows(official, load_cleveland_nowcasts(db, nowcast_start), policy)
    if not rows:
        raise RuntimeError("Inflation calculation produced no rows")
    stored = save_automatic(db, rows)
    latest = db.request("GET", "us_inflation_monthly", params={
        "select": "month,status,model_version", "order": "month.desc", "limit": "1",
    }) or []
    if not latest or latest[0].get("model_version") != MODEL_VERSION:
        raise RuntimeError("Post-write inflation verification failed")
    print(json.dumps({"mode": "automatic", "model_version": MODEL_VERSION,
                      "calculated": len(rows), "stored": stored, "latest": latest[0]}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("sources", "derived", "all"), default="all")
    args = parser.parse_args()
    if args.stage in ("sources", "all"):
        collect_sources()
    if args.stage in ("derived", "all"):
        run_automatic()


if __name__ == "__main__":
    main()
