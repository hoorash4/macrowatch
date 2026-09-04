"""Inspect sector-specific Financial Services Commission datasets without database writes."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from typing import Any

import requests


TITLE_CANDIDATES = {
    "bank": (
        "은행_재무현황_요약손익계산서",
        "은행_재무현황_요약손익계산서(은행)",
        "은행_재무현황_요약재무상태표(자산-은행)",
    ),
    "holding": (
        "금융지주_재무현황_요약연결손익계산서",
        "금융지주_재무현황_요약손익계산서",
        "금융지주_재무현황_요약연결재무상태표(자산)",
    ),
    "life": (
        "생보_재무현황_요약손익계산서",
        "생보_재무현황_요약손익계산서(전체)",
        "생보_재무현황_요약재무상태표(자산-전체)",
    ),
    "nonlife": (
        "손보_재무현황_요약손익계산서",
        "손보_재무현황_요약손익계산서(전체)",
        "손보_재무현황_요약재무상태표(자산-전체)",
    ),
    "card": (
        "신용카드_재무현황_요약손익계산서",
        "신용카드_재무현황_요약손익계산서(07.12월이후)",
        "신용카드_재무현황_요약재무상태표(자산)",
    ),
    "securities": (
        "증권_재무현황_요약손익계산서",
        "증권_재무현황_요약손익계산서(07.03이후)",
        "증권_재무현황_요약재무상태표(자산)",
    ),
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Inspect sector-specific financial-statistics schemas")
    result.add_argument("--year", type=int, required=True, choices=(2016, 2017, 2018))
    result.add_argument("--quarter", type=int, required=True, choices=(1, 2, 3, 4))
    return result


def _titles(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({
        str(row.get("title") or "").strip()
        for row in rows
        if str(row.get("title") or "").strip()
    })


def main() -> None:
    args = parser().parse_args()
    required = {
        "SUPABASE_URL": os.getenv("SUPABASE_URL", "").strip(),
        "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
        "EARNINGS_FINANCIAL_SOURCE_TOKEN": os.getenv("EARNINGS_FINANCIAL_SOURCE_TOKEN", "").strip(),
        "DATA_GO_KR_SERVICE_KEY": os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"Missing {', '.join(missing)}")

    base_month = f"{args.year}{args.quarter * 3:02d}"
    endpoint = f"{required['SUPABASE_URL'].rstrip('/')}/functions/v1/earnings-financial-company-source"
    headers = {
        "Authorization": f"Bearer {required['EARNINGS_FINANCIAL_SOURCE_TOKEN']}",
        "apikey": required["SUPABASE_SERVICE_ROLE_KEY"],
        "X-Public-Data-API-Key": required["DATA_GO_KR_SERVICE_KEY"],
        "Content-Type": "application/json",
    }
    for sector, titles in TITLE_CANDIDATES.items():
        probes = ((None, None), *((base_month, title) for title in titles))
        for requested_month, title in probes:
            try:
                request_body = {
                    "mode": "sector_financial",
                    "sector": sector,
                }
                if requested_month:
                    request_body["bas_ym"] = requested_month
                if title:
                    request_body["title"] = title
                if not requested_month and not title:
                    request_body["num_of_rows"] = 100
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=request_body,
                    timeout=(5, 35),
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("rows") if isinstance(payload, dict) else None
                rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
                field_names = sorted({
                    key for row in rows for key in row
                    if key not in {"crno", "fncoCd", "fncoNm", "basYm", "title"}
                })
                base_months = sorted({
                    str(row.get("basYm") or "").strip() for row in rows
                    if str(row.get("basYm") or "").strip()
                })
                print(json.dumps({
                    "stage": "financial_sector_schema",
                    "sector": sector,
                    "base_month": requested_month,
                    "requested_title": title,
                    "status": payload.get("status"),
                    "row_count": len(rows),
                    "payload_shape": payload.get("payload_shape"),
                    "titles": _titles(rows),
                    "base_month_range": [base_months[0], base_months[-1]] if base_months else [],
                    "field_names": field_names,
                    "sample_companies": sorted({
                        str(row.get("fncoNm") or "").strip() for row in rows
                        if str(row.get("fncoNm") or "").strip()
                    })[:10],
                }, ensure_ascii=False), flush=True)
                if title and rows:
                    break
            except requests.RequestException as error:
                print(json.dumps({
                    "stage": "financial_sector_schema",
                    "sector": sector,
                    "base_month": requested_month,
                    "requested_title": title,
                    "status": "transport_error",
                    "error_type": type(error).__name__,
                    "http_status": getattr(error.response, "status_code", None),
                }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
