from __future__ import annotations

import argparse
import json

from .pipeline import KoreaEarningsV2Pipeline


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="MacroWatch Earnings V2 Korean quarterly pipeline")
    result.add_argument("--year", type=int, required=True)
    result.add_argument("--quarter", type=int, choices=(1, 2, 3, 4))
    result.add_argument("--write", action="store_true", help="검증을 통과한 결과를 V2 DB에 저장")
    result.add_argument("--allow-review", action="store_true", help="누락 행을 review_required로 저장")
    return result


def main() -> None:
    args = parser().parse_args()
    pipeline = KoreaEarningsV2Pipeline.from_env()
    if args.quarter:
        result = pipeline.run_quarter(args.year, args.quarter, write=args.write, allow_review=args.allow_review)
    else:
        result = pipeline.run_year(args.year, write=args.write, allow_review=args.allow_review)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
