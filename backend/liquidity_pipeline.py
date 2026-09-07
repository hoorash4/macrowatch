"""Official-data liquidity composites. Existing stress/earnings paths are untouched."""
from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import math
import time
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone

import requests

from common import SupabaseRest, fetch_fred_observations, require_env

VERSION = "liquidity-v1"
START = date(2021, 9, 7)  # Initial five-year history; never move this retention boundary.
KR_PRESSURE_START = date(2021, 11, 25)
SOURCE_START = date(2016, 8, 1)  # Calibration only; not displayed as backfill.
WEIGHTS = {
    ("US", "pressure"): {"secured_spread": .5, "unsecured_spread": .25, "dispersion": .25},
    ("US", "capacity"): {"reserve_ratio": .5, "reserve_change": .25, "rrp_ratio": .25},
    ("KR", "pressure"): {"call_spread": .5, "repo_spread": .5},
    ("KR", "capacity"): {"m2_change": .25, "lf_change": .25, "equity_flow": .25, "bond_flow": .25},
}
FRED = {
    "sofr": "SOFR", "effr": "EFFR", "iorb": "IORB", "p25": "SOFR25", "p75": "SOFR75",
    "reserves": "WRESBAL", "assets": "TLAACBW027SBOG", "rrp": "RRPONTSYD",
}
ECOS = {
    "kofr": ("817Y002", "D", "010901000"),
    "equity_flow": ("301Y013", "M", "BOPF22100000"),
    "bond_flow": ("301Y013", "M", "BOPF22200000"),
}
SNAPSHOTS = {849: {"기준금리": "base", "콜금리(익일물)": "call"},
             875: {"M2(평잔, 좌축)": "m2"}, 876: {"Lf(평잔, 좌축)": "lf"}}


def shift_month(day: date, delta: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + delta, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def get_json(session, url, *, params=None):
    """Retry transport/rate-limit/server failures only; never accept an empty success."""
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=(12, 45))
            if response.status_code not in (429, 500, 502, 503, 504):
                response.raise_for_status()
                return response.json()
        except (requests.ConnectionError, requests.Timeout):
            pass
        if attempt < 2:
            time.sleep(2 ** attempt)
    # Do not print ECOS request URLs, which contain a key.
    raise RuntimeError("Official source unavailable after three transport/server attempts")


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def parse_snapshot(payload, mapping, start, end):
    data = payload.get("data", {})
    reader = csv.DictReader(io.StringIO(data.get("chart_opt", {}).get("data", {}).get("csv", "")))
    if not set(mapping).issubset(reader.fieldnames or []):
        raise RuntimeError("BOK snapshot column contract changed")
    result = {name: {} for name in mapping.values()}
    for row in reader:
        day = datetime.fromtimestamp(float(row["period"]) / 1000, timezone.utc).date()
        if start <= day <= end:
            for column, name in mapping.items():
                value = finite(row[column])
                if value is not None:
                    result[name][day] = value
    if any(not values for values in result.values()):
        raise RuntimeError("BOK snapshot returned no observations")
    return result


def collect(country, existing, end):
    result = {name: dict(values) for name, values in existing.items()}
    session = requests.Session()
    session.headers.update({"User-Agent": "MacroWatch liquidity research", "Referer": "https://snapshot.bok.or.kr/"})

    def begin(name):
        return max(result[name]) + timedelta(days=1) if result.get(name) else SOURCE_START

    if country == "US":
        key = require_env("FRED_API_KEY")
        for name, series in FRED.items():
            start = begin(name)
            if start > end:
                continue
            rows = fetch_fred_observations(series, key, start=start.isoformat(), end=end.isoformat())
            values = {date.fromisoformat(row["date"]): finite(row["value"]) for row in rows}
            result.setdefault(name, {}).update({day: value for day, value in values.items() if value is not None})
            print(f"source={series} new={sum(v is not None for v in values.values())}", flush=True)
    else:
        for chart, mapping in SNAPSHOTS.items():
            payload = get_json(session, f"https://snapshot.bok.or.kr/api/chart/getChart?id={chart}")
            parsed = parse_snapshot(payload, mapping, SOURCE_START, end)
            for name, values in parsed.items():
                # Previously stored observations are trusted in automatic runs.
                result[name] = {**values, **result.get(name, {})}
            print(f"source=BOK-{chart} rows={sum(map(len, parsed.values()))}", flush=True)
        key = require_env("ECOS_API_KEY")
        for name, (stat, cycle, item) in ECOS.items():
            start = begin(name)
            if cycle == "M":
                start = shift_month(max(result[name]), 1) if result.get(name) else SOURCE_START
            if start > end:
                continue
            for year in range(start.year, end.year + 1):
                lower, upper = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
                fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
                offset = 1
                while True:
                    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/{offset}/{offset+99}/"
                           f"{stat}/{cycle}/{lower.strftime(fmt)}/{upper.strftime(fmt)}/{item}")
                    payload = get_json(session, url)
                    code = payload.get("RESULT", {}).get("CODE")
                    if code == "INFO-200":
                        break  # Not released / before series inception, not HTTP failure.
                    if code:
                        raise RuntimeError(f"ECOS {stat}/{item}: {code}")
                    block = payload.get("StatisticSearch")
                    if not block or not block.get("row"):
                        raise RuntimeError(f"ECOS {stat}/{item}: malformed empty response")
                    for row in block["row"]:
                        if row.get("ITEM_CODE1") != item:
                            raise RuntimeError("ECOS returned an unexpected item")
                        day = datetime.strptime(row["TIME"], fmt).date()
                        value = finite(row["DATA_VALUE"])
                        if value is not None:
                            result.setdefault(name, {})[day] = value
                    if offset + 99 >= int(block["list_total_count"]):
                        break
                    offset += 100
            print(f"source=ECOS-{name} rows={len(result.get(name, {}))}", flush=True)
    expected = set(FRED) if country == "US" else {"base", "call", "m2", "lf", *ECOS}
    if any(not result.get(name) for name in expected):
        raise RuntimeError("A required source has no observations; no results will be saved")
    # Source calendars differ. Do not pretend stale observations are today's data.
    monthly = {"m2", "lf", "equity_flow", "bond_flow"}
    weekly = {"reserves", "assets"}
    for name in expected:
        allowance = 100 if name in monthly else 21 if name in weekly else 10
        if (end - max(result[name])).days > allowance:
            raise RuntimeError(f"Stale source: {name}, latest={max(result[name])}")
    return result


def asof(values, day, max_age):
    dates = sorted(values)
    index = bisect_right(dates, day) - 1
    if index < 0 or (day - dates[index]).days > max_age:
        return None
    return values[dates[index]]


def features(country, data):
    pressure, capacity = {}, {}
    if country == "US":
        for day in sorted(set(data["sofr"]) & set(data["effr"]) & set(data["p25"]) & set(data["p75"])):
            rate = asof(data["iorb"], day, 7)
            if rate is not None:
                pressure[day] = {"secured_spread": data["sofr"][day] - rate,
                                 "unsecured_spread": data["effr"][day] - rate,
                                 "dispersion": data["p75"][day] - data["p25"][day]}
        for day, reserves in sorted(data["reserves"].items()):
            assets, rrp = asof(data["assets"], day, 7), asof(data["rrp"], day, 7)
            previous = asof(data["reserves"], day - timedelta(days=28), 7)
            if assets and previous and rrp is not None:
                # WRESBAL is USD millions; assets and ON RRP are USD billions.
                capacity[day] = {"reserve_ratio": reserves / 1000 / assets * 100,
                                 "reserve_change": (reserves / previous - 1) * 100,
                                 "rrp_ratio": rrp / assets * 100}
    else:
        for day in sorted(set(data["call"]) & set(data["kofr"])):
            rate = asof(data["base"], day, 7)
            if rate is not None:
                pressure[day] = {"call_spread": data["call"][day] - rate,
                                 "repo_spread": data["kofr"][day] - rate}
        for day in sorted(set(data["m2"]) & set(data["lf"]) & set(data["equity_flow"]) & set(data["bond_flow"])):
            previous = shift_month(day, -3)
            if not data["m2"].get(previous) or not data["lf"].get(previous):
                continue
            months = [shift_month(day, -i) for i in range(3)]
            if not all(m in data[k] for m in months for k in ("equity_flow", "bond_flow")):
                continue
            capacity[day] = {"m2_change": (data["m2"][day] / data["m2"][previous] - 1) * 100,
                             "lf_change": (data["lf"][day] / data["lf"][previous] - 1) * 100,
                             "equity_flow": sum(data["equity_flow"][m] for m in months),
                             "bond_flow": sum(data["bond_flow"][m] for m in months)}
    return {"pressure": pressure, "capacity": capacity}


def percentile(value, history):
    """Midrank handles repeated policy-rate spreads without arbitrary 0/100 jumps."""
    if not history or not math.isfinite(value):
        raise ValueError("Non-empty finite observations required")
    return 100 * (sum(x < value for x in history) + .5 * sum(x == value for x in history)) / len(history)


def calculate(country, data):
    output = []
    for metric, observations in features(country, data).items():
        dates = sorted(observations)
        weights = WEIGHTS[country, metric]
        for i, day in enumerate(dates):
            boundary = shift_month(day, -60)
            history = dates[bisect_right(dates, boundary):i+1]
            minimum = KR_PRESSURE_START if (country, metric) == ("KR", "pressure") else START
            if day < minimum:
                continue
            scores = {name: percentile(observations[day][name], [observations[d][name] for d in history])
                      for name in weights}
            output.append({"country": country, "metric": metric, "observation_date": day.isoformat(),
                           "score": round(sum(scores[k] * w for k, w in weights.items()), 4),
                           "components": observations[day], "component_scores": scores,
                           "sample_count": len(history), "is_warmup": len(history) < (12 if country == "KR" and metric == "capacity" else 60),
                           "frequency": "M" if country == "KR" and metric == "capacity" else "W" if metric == "capacity" else "D",
                           "method_version": VERSION})
    for metric in ("pressure", "capacity"):
        rows = [r for r in output if r["metric"] == metric]
        if not rows:
            raise RuntimeError(f"No complete {country}/{metric} observations")
        expected = KR_PRESSURE_START if (country, metric) == ("KR", "pressure") else START
        allowance = 35 if metric == "capacity" and country == "KR" else 10
        if (date.fromisoformat(rows[0]["observation_date"]) - expected).days > allowance:
            raise RuntimeError(f"History coverage missing for {country}/{metric}: {rows[0]['observation_date']}")
    return output


def load_existing(db, country):
    data, offset = {}, 0
    while True:
        page = db.request("GET", "liquidity_observations", params={"country": f"eq.{country}",
                          "select": "series,observation_date,value", "order": "series,observation_date",
                          "offset": str(offset), "limit": "1000"})
        for row in page:
            data.setdefault(row["series"], {})[date.fromisoformat(row["observation_date"])] = float(row["value"])
        if len(page) < 1000:
            return data
        offset += len(page)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", choices=("US", "KR"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = None if args.dry_run else SupabaseRest(timeout=120)
    existing = {} if db is None else load_existing(db, args.country)
    data = collect(args.country, existing, date.today())
    results = calculate(args.country, data)
    raw = [{"country": args.country, "series": series, "observation_date": day.isoformat(), "value": value}
           for series, values in data.items() for day, value in values.items() if day not in existing.get(series, {})]
    if db:
        # One database transaction: no partially published country on failure.
        db.request("POST", "rpc/store_liquidity_batch", body={"p_country": args.country, "p_raw": raw, "p_results": results})
        for metric in ("pressure", "capacity"):
            latest = db.request("GET", "liquidity_indices", params={"country": f"eq.{args.country}",
                                "metric": f"eq.{metric}", "order": "observation_date.desc", "limit": "1"})
            expected = max(r["observation_date"] for r in results if r["metric"] == metric)
            if not latest or latest[0]["observation_date"] != expected:
                raise RuntimeError("Post-write verification failed")
    print(json.dumps({"country": args.country, "saved": bool(db), "new_raw": len(raw), "metrics": {
        metric: {"count": len([r for r in results if r["metric"] == metric]),
                 "first": min(r["observation_date"] for r in results if r["metric"] == metric),
                 "latest": max(r["observation_date"] for r in results if r["metric"] == metric)}
        for metric in ("pressure", "capacity")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
