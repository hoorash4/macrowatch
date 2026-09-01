from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .pipeline import KoreaEarningsV2Pipeline


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="MacroWatch Earnings V2 Korean quarterly pipeline")
    result.add_argument("--year", type=int, required=True)
    result.add_argument("--quarter", type=int, choices=(1, 2, 3, 4))
    result.add_argument("--write", action="store_true", help="기업군·부분 실적·잠정 또는 확정 집계를 V2 DB에 저장")
    result.add_argument("--allow-review", action="store_true", help="불완전 분기 뒤의 다음 분기도 계속 검사")
    return result


def completed_successfully(result: dict[str, Any] | list[dict[str, Any]]) -> bool:
    """GitHub Actions가 불완전 수집을 성공으로 오인하지 않게 한다."""
    rows = result if isinstance(result, list) else [result]
    return bool(rows) and all(row.get("status") == "ready" for row in rows)


def main() -> None:
    args = parser().parse_args()
    pipeline = KoreaEarningsV2Pipeline.from_env()
    if args.quarter:
        result = pipeline.run_quarter(args.year, args.quarter, write=args.write, allow_review=args.allow_review)
    else:
        result = pipeline.run_year(args.year, write=args.write, allow_review=args.allow_review)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    if not completed_successfully(result):
        sys.exit(2)


if __name__ == "__main__":
    main()
