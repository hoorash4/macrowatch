"""Inspect sector-specific Financial Services Commission datasets without database writes."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote

import requests


SECTOR_ENDPOINTS = {
    "domestic_bank": (
        "https://apis.data.go.kr/1160100/service/"
        "GetDomBankInfoService/getDomBankFinaInfo"
    ),
    "financial_holding": (
        "https://apis.data.go.kr/1160100/service/"
        "GetFinaHoldCompInfoService/getFinaHoldCompFinaInfo"
    ),
    "life_insurance": (
        "https://apis.data.go.kr/1160100/service/"
        "GetLifeInsuCompInfoService/getLifeInsuCompFinaInfo"
    ),
    "non_life_insurance": (
        "https://apis.data.go.kr/1160100/service/"
        "GetFireInsuCompInfoService/getFireInsuCompFinaInfo"
    ),
    "credit_card": (
        "https://apis.data.go.kr/1160100/service/"
        "GetCreditCardCompInfoService/getCreditCardCompFinaInfo"
    ),
    "securities": (
        "https://apis.data.go.kr/1160100/service/"
        "GetSecuCompInfoService/getSecuCompFinaInfo"
    ),
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Inspect registered sector-specific financial-statistics schemas",
    )
    result.add_argument("--year", type=int, required=True, choices=(2016, 2017, 2018))
    result.add_argument("--quarter", type=int, required=True, choices=(1, 2, 3, 4))
    return result


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response")
    body = response.get("body") if isinstance(response, dict) else None
    items = body.get("items") if isinstance(body, dict) else None
    item = items.get("item") if isinstance(items, dict) else None
    if isinstance(item, list):
        return [row for row in item if isinstance(row, dict)]
    return [item] if isinstance(item, dict) else []


def _error_code(payload: dict[str, Any]) -> str | None:
    response = payload.get("response")
    header = response.get("header") if isinstance(response, dict) else None
    code = str(header.get("resultCode") or "") if isinstance(header, dict) else ""
    return None if code in {"", "00", "000"} else code


def _titles(rows: Iterable[dict[str, Any]]) -> list[str]:
    values = {
        str(row.get("title") or "").strip()
        for row in rows
        if str(row.get("title") or "").strip()
    }
    return sorted(values)


def main() -> None:
    args = parser().parse_args()
    api_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not api_key:
        raise SystemExit("Missing DATA_GO_KR_SERVICE_KEY")

    base_month = f"{args.year}{args.quarter * 3:02d}"
    for sector, endpoint in SECTOR_ENDPOINTS.items():
        try:
            response = requests.get(
                endpoint,
                params={
                    "serviceKey": unquote(api_key),
                    "resultType": "json",
                    "pageNo": "1",
                    "numOfRows": "9999",
                    "basYm": base_month,
                },
                timeout=(5, 30),
            )
            response.raise_for_status()
            payload = response.json()
            error = _error_code(payload)
            rows = _items(payload)
            field_names = sorted({
                key for row in rows for key in row
                if key not in {"crno", "fncoCd", "fncoNm", "basYm", "title"}
            })
            print(json.dumps({
                "stage": "financial_sector_schema",
                "sector": sector,
                "base_month": base_month,
                "status": "source_error" if error else "ok",
                "source_error": error,
                "row_count": len(rows),
                "titles": _titles(rows),
                "field_names": field_names,
            }, ensure_ascii=False), flush=True)
        except requests.RequestException as error:
            print(json.dumps({
                "stage": "financial_sector_schema",
                "sector": sector,
                "base_month": base_month,
                "status": "transport_error",
                "error_type": type(error).__name__,
            }, ensure_ascii=False), flush=True)
        except ValueError:
            print(json.dumps({
                "stage": "financial_sector_schema",
                "sector": sector,
                "base_month": base_month,
                "status": "invalid_json",
            }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
