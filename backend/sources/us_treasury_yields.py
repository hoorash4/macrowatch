"""Official U.S. Treasury daily nominal and real par-yield data.

The Treasury XML feed is the authoritative live/history source for Treasury yields used by
MacroWatch.  FRED remains available only for non-Treasury series that have no equivalent
first-party Treasury feed.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree

import requests

from common import request_with_retry

FEED_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
NOMINAL_DATASET = "daily_treasury_yield_curve"
REAL_DATASET = "daily_treasury_real_yield_curve"
NOMINAL_SOURCE = "USTREASURY:daily_treasury_yield_curve"
REAL_SOURCE = "USTREASURY:daily_treasury_real_yield_curve"

NOMINAL_FIELDS = {
    "3M": "BC_3MONTH",
    "2Y": "BC_2YEAR",
    "10Y": "BC_10YEAR",
}
REAL_FIELDS = {
    "10Y": "TC_10YEAR",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _observation_date(raw_date: str) -> date | None:
    try:
        return datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def parse_treasury_values_xml(
    xml_text: str,
    start: date,
    end: date,
    fields: dict[str, str],
) -> dict[str, dict[date, float]]:
    """Parse Treasury's OData-style XML into maturity -> date/value maps."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise RuntimeError(f"Treasury yield XML parse failed: {error}") from error

    result: dict[str, dict[date, float]] = {maturity: {} for maturity in fields}
    for properties in (node for node in root.iter() if _local_name(node.tag) == "properties"):
        values = {_local_name(child.tag): (child.text or "").strip() for child in properties}
        raw_date = values.get("NEW_DATE") or values.get("Date")
        if not raw_date:
            continue
        observed = _observation_date(raw_date)
        if observed is None or not (start <= observed <= end):
            continue
        for maturity, field in fields.items():
            raw_value = values.get(field)
            if not raw_value:
                continue
            try:
                result[maturity][observed] = float(raw_value)
            except ValueError:
                continue
    return result


def parse_treasury_yield_xml(xml_text: str, start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    """Backward-compatible economic-chart parser for official 2Y and 10Y rows."""
    parsed = parse_treasury_values_xml(
        xml_text,
        start,
        end,
        {"2Y": NOMINAL_FIELDS["2Y"], "10Y": NOMINAL_FIELDS["10Y"]},
    )
    return {
        "US2Y": [{
            "series_code": "US2Y",
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "D",
            "source": NOMINAL_SOURCE,
        } for observed, value in sorted(parsed["2Y"].items())],
        "US10Y": [{
            "series_code": "US10Y",
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "D",
            "source": NOMINAL_SOURCE,
        } for observed, value in sorted(parsed["10Y"].items())],
    }


def _fetch_dataset_values(
    dataset: str,
    fields: dict[str, str],
    start: date,
    end: date,
) -> dict[str, dict[date, float]]:
    combined: dict[str, dict[date, float]] = {maturity: {} for maturity in fields}
    for year in range(start.year, end.year + 1):
        response = request_with_retry(lambda year=year: requests.get(
            FEED_URL,
            params={"data": dataset, "field_tdr_date_value": str(year)},
            headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
            timeout=45,
        ))
        response.raise_for_status()
        parsed = parse_treasury_values_xml(response.text, start, end, fields)
        for maturity in combined:
            combined[maturity].update(parsed[maturity])
    return combined


def fetch_treasury_nominal_values(
    start: date,
    end: date,
    maturities: tuple[str, ...] = ("3M", "2Y", "10Y"),
) -> dict[str, dict[date, float]]:
    fields = {maturity: NOMINAL_FIELDS[maturity] for maturity in maturities}
    values = _fetch_dataset_values(NOMINAL_DATASET, fields, start, end)
    for maturity in maturities:
        if not values[maturity]:
            raise RuntimeError(f"U.S. Treasury returned no usable nominal {maturity} rows.")
    return values


def fetch_treasury_real_values(
    start: date,
    end: date,
    maturities: tuple[str, ...] = ("10Y",),
) -> dict[str, dict[date, float]]:
    fields = {maturity: REAL_FIELDS[maturity] for maturity in maturities}
    values = _fetch_dataset_values(REAL_DATASET, fields, start, end)
    for maturity in maturities:
        if not values[maturity]:
            raise RuntimeError(f"U.S. Treasury returned no usable real {maturity} rows.")
    return values


def fetch_treasury_yield_rows(start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    """Economic-chart rows for 2Y/10Y nominal yields from the first-party Treasury feed."""
    values = fetch_treasury_nominal_values(start, end, ("2Y", "10Y"))
    return {
        "US2Y": [{
            "series_code": "US2Y",
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "D",
            "source": NOMINAL_SOURCE,
        } for observed, value in sorted(values["2Y"].items())],
        "US10Y": [{
            "series_code": "US10Y",
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "D",
            "source": NOMINAL_SOURCE,
        } for observed, value in sorted(values["10Y"].items())],
    }


def fetch_treasury_real_yield_rows(start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    """Economic-chart rows for the official 10Y real par yield."""
    values = fetch_treasury_real_values(start, end, ("10Y",))
    return {
        "US10Y_REAL": [{
            "series_code": "US10Y_REAL",
            "observation_date": observed.isoformat(),
            "value": value,
            "frequency": "D",
            "source": REAL_SOURCE,
        } for observed, value in sorted(values["10Y"].items())],
    }
