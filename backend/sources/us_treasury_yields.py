"""Official U.S. Treasury daily par-yield rows for live economic-chart collection."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree

import requests

from common import request_with_retry

FEED_URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
SOURCE = "USTREASURY:daily_treasury_yield_curve"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_treasury_yield_xml(xml_text: str, start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise RuntimeError(f"Treasury yield XML parse failed: {error}") from error

    rows: dict[str, list[dict[str, Any]]] = {"US2Y": [], "US10Y": []}
    for properties in (node for node in root.iter() if _local_name(node.tag) == "properties"):
        values = {_local_name(child.tag): (child.text or "").strip() for child in properties}
        raw_date = values.get("NEW_DATE") or values.get("Date")
        if not raw_date:
            continue
        try:
            observed = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                observed = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
        if not (start <= observed <= end):
            continue
        for code, field in (("US2Y", "BC_2YEAR"), ("US10Y", "BC_10YEAR")):
            raw_value = values.get(field)
            if not raw_value:
                continue
            try:
                value = float(raw_value)
            except ValueError:
                continue
            rows[code].append({
                "series_code": code,
                "observation_date": observed.isoformat(),
                "value": value,
                "frequency": "D",
                "source": SOURCE,
            })

    for code in rows:
        unique = {str(row["observation_date"]): row for row in rows[code]}
        rows[code] = [unique[key] for key in sorted(unique)]
    return rows


def fetch_treasury_yield_rows(start: date, end: date) -> dict[str, list[dict[str, Any]]]:
    combined: dict[str, list[dict[str, Any]]] = {"US2Y": [], "US10Y": []}
    for year in range(start.year, end.year + 1):
        response = request_with_retry(lambda year=year: requests.get(
            FEED_URL,
            params={"data": "daily_treasury_yield_curve", "field_tdr_date_value": str(year)},
            headers={"User-Agent": "Mozilla/5.0 MacroWatch/1.0"},
            timeout=45,
        ))
        response.raise_for_status()
        parsed = parse_treasury_yield_xml(response.text, start, end)
        for code in combined:
            combined[code].extend(parsed[code])

    for code in combined:
        unique = {str(row["observation_date"]): row for row in combined[code]}
        combined[code] = [unique[key] for key in sorted(unique)]
        if not combined[code]:
            raise RuntimeError(f"U.S. Treasury returned no usable {code} rows.")
    return combined
