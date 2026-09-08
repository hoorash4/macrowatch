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

VERSION = "liquidity-monthly-v2"
US_VERSION = "us-equity-environment-weekly-v2"
KR_VERSION = "kr-equity-environment-weekly-v1"
US_START = date(2021, 11, 1)
KR_START = date(2021, 12, 17)
KR_MOMENTUM_START = date(2022, 3, 18)
ENVIRONMENT_DIRECTIONS = {
    "US": {"real_yield": -1, "credit_conditions": -1, "net_supply_change": 1, "funding_spread": -1},
    "KR": {"funding_spread": -1, "liquidity_growth": 1, "equity_flow": 1, "won_strength": 1},
}
US_DIRECTIONS = ENVIRONMENT_DIRECTIONS["US"]
START = date(2021, 9, 7)  # Initial five-year history; never move this retention boundary.
KR_PRESSURE_START = date(2021, 11, 25)
SOURCE_START = date(2016, 8, 1)  # Calibration only; not displayed as backfill.
WEIGHTS = {
    ("US", "environment"): {key: .25 for key in US_DIRECTIONS},
    ("US", "momentum"): {key: .25 for key in US_DIRECTIONS},
    ("KR", "environment"): {key: .25 for key in ENVIRONMENT_DIRECTIONS["KR"]},
    ("KR", "momentum"): {key: .25 for key in ENVIRONMENT_DIRECTIONS["KR"]},
    ("KR", "pressure"): {"call_spread": .5, "repo_spread": .5},
    ("KR", "capacity"): {"m2_change": .25, "lf_change": .25, "equity_flow": .25, "bond_flow": .25},
}
FRED = {
    "sofr": "SOFR", "iorb": "IORB", "ioer": "IOER", "rrp": "RRPONTSYD",
    "real_yield": "DFII10", "credit_conditions": "NFCICREDIT",
    "fed_assets": "WALCL", "tga": "WTREGEN",
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
            if name == "ioer" and result.get(name):
                continue  # Discontinued in July 2021; used only to calibrate pre-IORB history.
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
    weekly = {"fed_assets", "tga", "credit_conditions"}
    for name in expected:
        if name == "ioer":
            continue
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
        return {"environment": equity_environment_features(data)}
    if "foreign_flow_ratio" in data:
        return {"environment": korea_equity_environment_features(data)}
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


def equity_environment_features(data):
    """Funding environment proxies, not equity inflows or return forecasts."""
    net = {}
    for day, assets in sorted(data["fed_assets"].items()):
        tga, rrp = asof(data["tga"], day, 14), asof(data["rrp"], day, 7)
        if tga is not None and rrp is not None:
            # WALCL and WTREGEN: USD millions. ON RRP: USD billions.
            net[day] = assets - tga - rrp * 1000
    supply = {}
    for day, value in net.items():
        previous = asof(net, day - timedelta(days=91), 14)
        if previous is not None and previous > 0:
            supply[day] = (value / previous - 1) * 100
    output = {}
    for day, real_yield in sorted(data["real_yield"].items()):
        credit = asof(data["credit_conditions"], day, 14)
        flow = asof(supply, day, 14)
        sofr = asof(data["sofr"], day, 7)
        rate = asof(data["iorb"], day, 7)
        if rate is None:
            rate = asof(data["ioer"], day, 7)
        if all(value is not None for value in (credit, flow, sofr, rate)):
            output[day] = {"real_yield": real_yield, "credit_conditions": credit,
                           "net_supply_change": flow, "funding_spread": sofr - rate}
    return output


def korea_equity_environment_features(data):
    """Korean equity funding conditions using existing official and daily flow inputs."""
    money_growth = {}
    for day in sorted(set(data["m2"]) & set(data["lf"])):
        previous = shift_month(day, -3)
        if data["m2"].get(previous) and data["lf"].get(previous):
            money_growth[day] = ((data["m2"][day] / data["m2"][previous] - 1) * 100
                                 + (data["lf"][day] / data["lf"][previous] - 1) * 100) / 2
    output = {}
    for day, equity_flow in sorted(data["foreign_flow_ratio"].items()):
        call, repo = asof(data["call"], day, 7), asof(data["kofr"], day, 7)
        base, liquidity = asof(data["base"], day, 14), asof(money_growth, day, 100)
        won_return = data["usdkrw_return"].get(day)
        if all(value is not None for value in (call, repo, base, liquidity, won_return)):
            output[day] = {"funding_spread": ((call - base) + (repo - base)) / 2,
                           "liquidity_growth": liquidity, "equity_flow": equity_flow,
                           "won_strength": -won_return}
    return output


def calculate_equity_environment(country, data, end):
    observations = weekly_smoothed_features(features(country, data)["environment"], end)
    directions = ENVIRONMENT_DIRECTIONS[country]
    start = US_START if country == "US" else KR_START
    version = US_VERSION if country == "US" else KR_VERSION
    dates = sorted(observations)
    output = []
    for index, day in enumerate(dates):
        if day < start:
            continue
        history = dates[bisect_right(dates, day - timedelta(weeks=260)):index + 1]
        levels = {}
        for name, direction in directions.items():
            value = observations[day][name]
            rank = percentile(value, [observations[d][name] for d in history])
            levels[name] = rank if direction > 0 else 100 - rank
        output.append({"country": country, "metric": "environment", "observation_date": day.isoformat(),
                       "score": round(sum(levels[k] * w for k, w in WEIGHTS[country, "environment"].items()), 4),
                       "components": observations[day], "component_scores": levels,
                       "sample_count": len(history), "is_warmup": len(history) < 52,
                       "frequency": "W", "method_version": version})
        momentum_start = start if country == "US" else KR_MOMENTUM_START
        if day < momentum_start:
            continue
        previous_day = day - timedelta(weeks=13)
        if previous_day not in observations:
            raise RuntimeError(f"Missing 13-week baseline for {country} environment: {day}")
        changes, change_scores = {}, {}
        for name, direction in directions.items():
            value = observations[day][name]
            changes[name] = direction * (value - observations[previous_day][name])
            past_changes = [observations[d][name] - observations[d - timedelta(weeks=13)][name]
                            for d in history if d - timedelta(weeks=13) in observations]
            rms = math.sqrt(sum(v * v for v in past_changes) / len(past_changes)) if past_changes else 0
            # Exactly 50 for no change. Changes in rolling ranks cannot imply improvement.
            change_scores[name] = 50 if not rms else 50 + 50 * math.tanh(changes[name] / (2 * rms))
        output.append({"country": country, "metric": "momentum", "observation_date": day.isoformat(),
                       "score": round(sum(change_scores[k] * w for k, w in WEIGHTS[country, "momentum"].items()), 4),
                       "components": changes, "component_scores": change_scores,
                       "sample_count": len(history), "is_warmup": len(history) < 52,
                       "frequency": "W", "method_version": version})
    expected_last = end - timedelta(days=end.weekday() + 3)
    for metric in ("environment", "momentum"):
        metric_start = start if metric == "environment" or country == "US" else KR_MOMENTUM_START
        expected_dates = set()
        cursor = metric_start + timedelta(days=(4 - metric_start.weekday()) % 7)
        while cursor <= expected_last:
            expected_dates.add(cursor.isoformat())
            cursor += timedelta(weeks=1)
        actual = {row["observation_date"] for row in output if row["metric"] == metric}
        if actual != expected_dates:
            raise RuntimeError(f"Incomplete {country}/{metric} weekly coverage: {sorted(expected_dates - actual)}")
    return output


def percentile(value, history):
    """Midrank handles repeated policy-rate spreads without arbitrary 0/100 jumps."""
    if not history or not math.isfinite(value):
        raise ValueError("Non-empty finite observations required")
    return 100 * (sum(x < value for x in history) + .5 * sum(x == value for x in history)) / len(history)


def monthly_features(observations, end):
    """Average raw features before ranking; publish closed months only."""
    grouped = {}
    for day, values in sorted(observations.items()):
        month = day.replace(day=1)
        if shift_month(month, 1) > end:
            continue
        grouped.setdefault(month, []).append(values)
    return {month: {key: sum(row[key] for row in rows) / len(rows)
                    for key in rows[0]} for month, rows in grouped.items()}


def weekly_smoothed_features(observations, end):
    """Publish completed ISO weeks as Friday-dated four-week moving averages."""
    last_closed_friday = end - timedelta(days=end.weekday() + 3)
    grouped = {}
    for day, values in sorted(observations.items()):
        week_end = day + timedelta(days=4 - day.weekday())
        if week_end > last_closed_friday:
            continue
        grouped.setdefault(week_end, []).append(values)
    weekly = {week: {key: sum(row[key] for row in rows) / len(rows)
                     for key in rows[0]} for week, rows in grouped.items()}
    smoothed = {}
    for week in sorted(weekly):
        window = [week - timedelta(weeks=offset) for offset in range(4)]
        if not all(item in weekly for item in window):
            continue
        smoothed[week] = {key: sum(weekly[item][key] for item in window) / len(window)
                          for key in weekly[week]}
    return smoothed


def calculate(country, data, end=None):
    end = end or date.today()
    if country == "US" or (country == "KR" and "foreign_flow_ratio" in data):
        return calculate_equity_environment(country, data, end)
    output = []
    for metric, observations in features(country, data).items():
        observations = monthly_features(observations, end)
        dates = sorted(observations)
        weights = WEIGHTS[country, metric]
        for i, day in enumerate(dates):
            boundary = shift_month(day, -60)
            history = dates[bisect_right(dates, boundary):i+1]
            minimum = KR_PRESSURE_START if (country, metric) == ("KR", "pressure") else START
            if day < minimum.replace(day=1):
                continue
            scores = {name: percentile(observations[day][name], [observations[d][name] for d in history])
                      for name in weights}
            output.append({"country": country, "metric": metric, "observation_date": day.isoformat(),
                           "score": round(sum(scores[k] * w for k, w in weights.items()), 4),
                           "components": observations[day], "component_scores": scores,
                           "sample_count": len(history), "is_warmup": len(history) < 12,
                           "frequency": "M",
                           "method_version": VERSION})
    for metric in ("pressure", "capacity"):
        rows = [r for r in output if r["metric"] == metric]
        if not rows:
            raise RuntimeError(f"No complete {country}/{metric} observations")
        expected = KR_PRESSURE_START if (country, metric) == ("KR", "pressure") else START
        allowance = 35
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


def load_korea_equity_context(db):
    data = {"foreign_flow_ratio": {}, "usdkrw_return": {}}
    offset = 0
    while True:
        page = db.request("GET", "korea_foreign_flow_daily", params={
            "select": "observation_date,foreign_flow_ratio,usdkrw_return",
            "order": "observation_date", "offset": str(offset), "limit": "1000"})
        for row in page:
            day = date.fromisoformat(row["observation_date"])
            data["foreign_flow_ratio"][day] = float(row["foreign_flow_ratio"])
            data["usdkrw_return"][day] = float(row["usdkrw_return"])
        if len(page) < 1000:
            break
        offset += len(page)
    if any(not values for values in data.values()):
        raise RuntimeError("Korean foreign-flow context has no observations")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", choices=("US", "KR"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SupabaseRest(timeout=120)
    existing = {} if args.dry_run else load_existing(db, args.country)
    data = collect(args.country, existing, date.today())
    if args.country == "KR":
        data.update(load_korea_equity_context(db))
    results = calculate(args.country, data)
    raw = [{"country": args.country, "series": series, "observation_date": day.isoformat(), "value": value}
           for series, values in data.items() if series not in ("foreign_flow_ratio", "usdkrw_return")
           for day, value in values.items() if day not in existing.get(series, {})]
    if not args.dry_run:
        # One database transaction: no partially published country on failure.
        db.request("POST", "rpc/store_liquidity_batch", body={"p_country": args.country, "p_raw": raw, "p_results": results})
        for metric in sorted({row["metric"] for row in results}):
            latest = db.request("GET", "liquidity_indices", params={"country": f"eq.{args.country}",
                                "metric": f"eq.{metric}", "method_version": f"eq.{US_VERSION if args.country == 'US' else KR_VERSION}", "order": "observation_date.desc", "limit": "1"})
            expected = max(r["observation_date"] for r in results if r["metric"] == metric)
            if not latest or latest[0]["observation_date"] != expected:
                raise RuntimeError("Post-write verification failed")
    print(json.dumps({"country": args.country, "saved": not args.dry_run, "new_raw": len(raw), "metrics": {
        metric: {"count": len([r for r in results if r["metric"] == metric]),
                 "first": min(r["observation_date"] for r in results if r["metric"] == metric),
                 "latest": max(r["observation_date"] for r in results if r["metric"] == metric)}
        for metric in sorted({row["metric"] for row in results})}}, ensure_ascii=False))


if __name__ == "__main__":
    main()

