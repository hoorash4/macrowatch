"""Official monthly inflation rates shared by automatic and backfill entrypoints.

Only year-over-year rates leave this module. Raw index levels are fetched solely as
the twelve-month calculation base and are never returned as chart-storage rows.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import requests

from common import request_with_retry, require_env


BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BEA_URL = "https://apps.bea.gov/api/data"
BEA_TABLE = "T20804"
KOSIS_SEARCH_URL = "https://kosis.kr/openapi/statisticsSearch.do"
KOSIS_DATA_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
ECOS_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
TIMEOUT_SECONDS = 60

BLS_SERIES = {
    "US_CPI": "CUSR0000SA0",
    "US_CORE_CPI": "CUSR0000SA0L1E",
    "US_PPI": "WPSFD49501",
    "US_CORE_PPI": "WPSFD49511",
}
BEA_SERIES = {
    "US_PCE": "1",
    "US_CORE_PCE": "25",
}
KOSIS_SERIES = {
    "KR_CPI": "총지수",
    "KR_CORE_CPI": "식료품 및 에너지제외지수",
}
KOSIS_TABLE = "월별 소비자물가 등락률"
ECOS_SERIES = {
    "KR_PPI": ("404Y014", "*AA", None),
    "KR_IMPORT_PRICE": ("401Y015", "*AA", "W"),
}
ALL_SERIES_CODES = (*BLS_SERIES, *BEA_SERIES, *KOSIS_SERIES, *ECOS_SERIES)


def _number(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", ".", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row(code: str, observed: date, value: float, source: str) -> dict[str, Any]:
    return {
        "series_code": code,
        "observation_date": observed.isoformat(),
        "value": value,
        "frequency": "M",
        "source": source,
    }


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _yoy_rows(
    code: str,
    levels: dict[date, float],
    start: date,
    end: date,
    source: str,
) -> list[dict[str, Any]]:
    rows = []
    for observed in sorted(levels):
        if not start <= observed <= end:
            continue
        prior = levels.get(_add_months(observed, -12))
        current = levels[observed]
        if prior is None or prior == 0:
            continue
        rows.append(_row(code, observed, round(100.0 * (current / prior - 1.0), 4), f"{source}:YoY"))
    return rows


def fetch_bls_rates(start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    """Fetch BLS levels in ten-year blocks and return twelve-month changes."""
    calculation_start = _add_months(start.replace(day=1), -12)
    levels = {code: {} for code in BLS_SERIES}
    for first_year in range(calculation_start.year, end.year + 1, 10):
        last_year = min(first_year + 9, end.year)
        response = request_with_retry(lambda: requests.post(
            BLS_URL,
            json={
                "seriesid": list(BLS_SERIES.values()),
                "startyear": str(first_year),
                "endyear": str(last_year),
            },
            headers={"User-Agent": "MacroWatch inflation rates/1.0"},
            timeout=TIMEOUT_SECONDS,
        ))
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS request failed: {payload.get('message')}")
        returned = {str(item.get("seriesID")): item for item in payload.get("Results", {}).get("series", [])}
        for code, series_id in BLS_SERIES.items():
            if series_id not in returned:
                raise RuntimeError(f"BLS response omitted {series_id}")
            for item in returned[series_id].get("data", []):
                period = str(item.get("period") or "")
                value = _number(item.get("value"))
                if not re.fullmatch(r"M(0[1-9]|1[0-2])", period) or value is None:
                    continue
                observed = date(int(item["year"]), int(period[1:]), 1)
                if calculation_start <= observed <= end:
                    levels[code][observed] = value
    return {
        code: _yoy_rows(code, values, start, end, f"BLS:{BLS_SERIES[code]}")
        for code, values in levels.items()
    }


def fetch_bea_index_levels(
    start: date,
    end: date,
    api_key: str | None = None,
) -> dict[str, dict[date, float]]:
    """Return raw PCE price levels for in-memory model calculations only."""
    years = ",".join(str(year) for year in range(start.year, end.year + 1))
    response = request_with_retry(lambda: requests.get(BEA_URL, params={
        "UserID": api_key or require_env("BEA_API_KEY"),
        "method": "GetData",
        "datasetname": "NIPA",
        "TableName": BEA_TABLE,
        "Frequency": "M",
        "Year": years,
        "ResultFormat": "JSON",
    }, timeout=TIMEOUT_SECONDS))
    response.raise_for_status()
    payload = response.json().get("BEAAPI", {})
    if payload.get("Error"):
        raise RuntimeError(f"BEA request failed: {payload['Error']}")
    data = (payload.get("Results") or {}).get("Data") or []
    levels = {code: {} for code in BEA_SERIES}
    by_line = {line_number: code for code, line_number in BEA_SERIES.items()}
    for item in data:
        code = by_line.get(str(item.get("LineNumber") or ""))
        period = str(item.get("TimePeriod") or "")
        value = _number(item.get("DataValue"))
        if code is None or not re.fullmatch(r"\d{4}M(0[1-9]|1[0-2])", period) or value is None:
            continue
        observed = date(int(period[:4]), int(period[-2:]), 1)
        if start <= observed <= end:
            levels[code][observed] = value
    return levels


def fetch_bea_rates(start: date, end: date, api_key: str | None = None) -> dict[str, list[dict[str, Any]]]:
    calculation_start = _add_months(start.replace(day=1), -12)
    levels = fetch_bea_index_levels(calculation_start, end, api_key)
    return {
        code: _yoy_rows(code, values, start, end, "BEA:NIPA/T20804")
        for code, values in levels.items()
    }


def _normalize_title(value: object) -> str:
    return re.sub(r"[\s，,]", "", str(value or ""))


def _resolve_kosis_table(title: str, api_key: str) -> str:
    response = request_with_retry(lambda: requests.get(KOSIS_SEARCH_URL, params={
        "method": "getList", "apiKey": api_key, "format": "json", "jsonVD": "Y",
        "searchNm": title, "orgId": "101", "startCount": "1", "resultCount": "100", "sort": "RANK",
    }, timeout=TIMEOUT_SECONDS))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"KOSIS table search failed: {payload}")
    wanted = _normalize_title(title)
    for item in payload:
        if str(item.get("ORG_ID")) == "101" and _normalize_title(item.get("TBL_NM")) == wanted:
            return str(item["TBL_ID"])
    raise RuntimeError(f"KOSIS table not found: {title}")


def fetch_kosis_rates(start: date, end: date, api_key: str | None = None) -> dict[str, list[dict[str, Any]]]:
    key = api_key or require_env("KOSIS_API_KEY")
    output = {code: [] for code in KOSIS_SERIES}
    table_id = _resolve_kosis_table(KOSIS_TABLE, key)
    response = request_with_retry(lambda: requests.get(KOSIS_DATA_URL, params={
        "method": "getList", "apiKey": key, "orgId": "101", "tblId": table_id,
        "objL1": "ALL", "objL2": "ALL", "itmId": "ALL", "format": "json", "jsonVD": "Y",
        "prdSe": "M", "startPrdDe": f"{start:%Y%m}", "endPrdDe": f"{end:%Y%m}",
    }, timeout=TIMEOUT_SECONDS))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"KOSIS {table_id} request failed: {payload}")
    seen = {code: set() for code in KOSIS_SERIES}
    for item in payload:
        period = str(item.get("PRD_DE") or "")
        value = _number(item.get("DT"))
        labels = _normalize_title(" ".join(
            str(item.get(field) or "")
            for field in ("ITM_NM", "C1_NM", "C2_NM", "C3_NM", "UNIT_NM")
        ))
        if not re.fullmatch(r"\d{6}", period) or value is None:
            continue
        if "전년동월비" not in labels:
            continue
        matching = [code for code, label in KOSIS_SERIES.items() if _normalize_title(label) in labels]
        if len(matching) != 1:
            continue
        code = matching[0]
        if period in seen[code]:
            raise RuntimeError(f"KOSIS {table_id} returned duplicate YoY rows for {code} {period}")
        seen[code].add(period)
        observed = date(int(period[:4]), int(period[4:]), 1)
        if start <= observed <= end:
            output[code].append(_row(code, observed, value, f"KOSIS:101/{table_id}:YoY"))
    return output


def fetch_ecos_rates(start: date, end: date, api_key: str | None = None) -> dict[str, list[dict[str, Any]]]:
    key = api_key or require_env("ECOS_API_KEY")
    calculation_start = _add_months(start.replace(day=1), -12)
    levels = {code: {} for code in ECOS_SERIES}
    for code, (table_id, item_id, item_code2) in ECOS_SERIES.items():
        url = f"{ECOS_URL}/{key}/json/kr/1/10000/{table_id}/M/{calculation_start:%Y%m}/{end:%Y%m}/{item_id}"
        if item_code2:
            url += f"/{item_code2}"
        response = request_with_retry(lambda: requests.get(url, timeout=TIMEOUT_SECONDS))
        response.raise_for_status()
        payload = response.json()
        if payload.get("RESULT"):
            raise RuntimeError(f"ECOS {table_id} request failed: {payload['RESULT'].get('MESSAGE')}")
        for item in ((payload.get("StatisticSearch") or {}).get("row") or []):
            period = str(item.get("TIME") or "")
            value = _number(item.get("DATA_VALUE"))
            if not re.fullmatch(r"\d{6}", period) or value is None:
                continue
            observed = date(int(period[:4]), int(period[4:]), 1)
            if calculation_start <= observed <= end:
                levels[code][observed] = value
    return {
        code: _yoy_rows(
            code,
            values,
            start,
            end,
            "ECOS:" + "/".join(str(part) for part in ECOS_SERIES[code] if part is not None),
        )
        for code, values in levels.items()
    }


def validate_complete(result: dict[str, list[dict[str, Any]]]) -> None:
    missing = [code for code in ALL_SERIES_CODES if not result.get(code)]
    if missing:
        raise RuntimeError(f"Inflation sources returned no usable rows: {', '.join(missing)}")
    for code, rows in result.items():
        dates = [str(row["observation_date"]) for row in rows]
        if len(dates) != len(set(dates)):
            raise RuntimeError(f"Inflation source returned duplicate months: {code}")


def fetch_all_rates(start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for provider_rows in (
        fetch_bls_rates(start, end),
        fetch_bea_rates(start, end),
        fetch_kosis_rates(start, end),
        fetch_ecos_rates(start, end),
    ):
        result.update(provider_rows)
    validate_complete(result)
    return result
