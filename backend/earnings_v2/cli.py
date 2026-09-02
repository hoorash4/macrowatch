from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .pipeline import KoreaEarningsV2Pipeline
from .runtime import execution_deadline


DAILY_DEADLINE_SECONDS = 240
QUARTER_DEADLINE_SECONDS = 600


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="MacroWatch Earnings V2 Korean quarterly pipeline")
    result.add_argument("--year", type=int)
    result.add_argument("--quarter", type=int, choices=(1, 2, 3, 4))
    result.add_argument("--daily", action="store_true", help="최근 완료 분기의 신규 공시·대기 기업만 갱신")
    result.add_argument("--recalculate-only", action="store_true", help="외부 API 호출 없이 저장된 분기값만 재집계")
    result.add_argument("--write", action="store_true", help="기업군·부분 실적·잠정 또는 확정 집계를 V2 DB에 저장")
    result.add_argument("--allow-review", action="store_true", help="불완전 분기 뒤의 다음 분기도 계속 검사")
    return result


def completed_successfully(result: dict[str, Any] | list[dict[str, Any]]) -> bool:
    """GitHub Actions가 불완전 수집을 성공으로 오인하지 않게 한다."""
    rows = result if isinstance(result, list) else [result]
    return bool(rows) and all(row.get("status") == "ready" for row in rows)


def main() -> None:
    argument_parser = parser()
    args = argument_parser.parse_args()
    pipeline = KoreaEarningsV2Pipeline.from_env()
    if args.daily and args.recalculate_only:
        argument_parser.error("--daily와 --recalculate-only는 함께 사용할 수 없습니다")
    default_deadline = DAILY_DEADLINE_SECONDS if args.daily else QUARTER_DEADLINE_SECONDS
    deadline_seconds = int(os.getenv("EARNINGS_V2_DEADLINE_SECONDS", str(default_deadline)))
    if args.daily:
        result = pipeline.run_daily(write=args.write, deadline_seconds=deadline_seconds)
    elif args.year is None:
        argument_parser.error("--daily가 아니면 --year가 필요합니다")
    elif args.quarter and args.recalculate_only:
        with execution_deadline(deadline_seconds):
            result = pipeline.recalculate_quarter(args.year, args.quarter, write=args.write)
    elif args.quarter:
        result = pipeline.run_quarter(
            args.year, args.quarter, write=args.write, allow_review=args.allow_review,
            deadline_seconds=deadline_seconds,
        )
    else:
        # 연도 실행도 분기마다 새 마감선을 받는다. 다년 백필은 이 경계를
        # 그대로 확장해 성공한 분기 다음부터 재개할 수 있다.
        result = pipeline.run_year(
            args.year, write=args.write, allow_review=args.allow_review,
            deadline_seconds=deadline_seconds,
        )
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    # 증분 실행의 provisional/collecting은 공급자 오류가 아니라 정상적인
    # 실적 발표 진행 상태다. 백필만 분기 미완료를 실패로 반환한다.
    if not args.daily and not args.recalculate_only and not completed_successfully(result):
        sys.exit(2)


if __name__ == "__main__":
    main()
