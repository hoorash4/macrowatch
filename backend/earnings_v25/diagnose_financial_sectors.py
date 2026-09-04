"""Inspect sector-specific Financial Services Commission datasets without database writes."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from typing import Any

import requests


INCOME_STATEMENT_TITLES = {
    "bank": "은행_재무현황_요약손익계산서",
    "holding": "금융지주_재무현황_요약연결손익계산서",
    "life": "생보_재무현황_요약손익계산서(전체)",
    "nonlife": "손보_재무현황_요약손익계산서(전체)",
    "card": "신용카드_재무현황_요약손익계산서(08.03월이후)",
    "securities": "증권_재무현황_요약손익계산서(11.06월이후)",
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
    probes = {
        "bank": (
            "은행_재무현황_주요자금조달운용_요약손익계산서(은행)",
            {"수익합계", "영업수익", "영업이익", "당기순이익", "당기순손익"},
        ),
        "life": (
            "생보_재무현황_요약손익계산서(전체)",
            {"보험손익_보험영업수익", "투자손익_투자영업수익", "특별계정손익_특별계정수익", "영업이익", "당기순이익"},
        ),
        "nonlife": (
            "손보_재무현황_요약손익계산서(전체)",
            {"보험손익_보험영업수익", "투자손익_투자영업수익", "특별계정이익_특별계정수익", "영업이익", "총영업이익", "당기순이익(또는 당기순손실)"},
        ),
    }
    for sector, (title, targets) in probes.items():
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json={
                    "mode": "sector_financial",
                    "sector": sector,
                    "bas_ym": base_month,
                    "title": title,
                    "num_of_rows": 9999,
                },
                timeout=(5, 45),
            )
            response.raise_for_status()
            payload = response.json()
            raw_rows = payload.get("rows") if isinstance(payload, dict) else None
            raw_rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
            rows = [row for row in raw_rows if str(row.get("basYm") or "") == base_month]
            matched: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                names = [
                    str(value).strip() for key, value in row.items()
                    if key.lower().endswith(("cdnm", "acitnm")) and str(value or "").strip()
                ]
                for name in names:
                    if name in targets and len(matched.setdefault(name, [])) < 2:
                        matched[name].append(row)
            print(json.dumps({
                "stage": "financial_sector_metric_probe",
                "sector": sector,
                "base_month": base_month,
                "status": "ok" if rows else "no_report",
                "requested_title": title,
                "row_count": len(rows),
                "matched": matched,
            }, ensure_ascii=False), flush=True)
        except requests.RequestException as error:
            print(json.dumps({
                "stage": "financial_sector_metric_probe",
                "sector": sector,
                "base_month": base_month,
                "status": "transport_error",
                "requested_title": title,
                "error_type": type(error).__name__,
                "http_status": getattr(error.response, "status_code", None),
            }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
