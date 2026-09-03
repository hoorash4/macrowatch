from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .pipeline import KoreaEarningsV2Pipeline
from .runtime import execution_deadline


QUARTER_DEADLINE_SECONDS = 600


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="MacroWatch Earnings V2.5 2016-2018 raw-DART backfill")
    result.add_argument("--year", type=int)
    result.add_argument("--quarter", type=int, choices=(1, 2, 3, 4))
    result.add_argument("--pending-only", action="store_true", help="지정한 과거 분기의 대기 기업만 재처리")
    result.add_argument("--recalculate-only", action="store_true", help="외부 API 호출 없이 저장된 분기값만 재집계")
    result.add_argument("--trust-previous-backfill", action="store_true", help="같은 백필에서 직전에 새로 저장한 누적 원자료 사용")
    result.add_argument("--write", action="store_true", help="기업군·부분 실적·잠정 또는 확정 집계를 V2 DB에 저장")
    result.add_argument("--require-complete", action="store_true", help="대기 기업이 하나라도 남으면 비정상 종료")
    return result


def completed_successfully(result: dict[str, Any] | list[dict[str, Any]]) -> bool:
    """처리가 끝난 잠정 데이터와 완결 데이터를 모두 실행 성공으로 본다."""
    rows = result if isinstance(result, list) else [result]
    return bool(rows) and all(row.get("status") in {"ready", "incomplete"} for row in rows)


def main() -> None:
    argument_parser = parser()
    args = argument_parser.parse_args()
    pipeline = KoreaEarningsV2Pipeline.from_env()
    if args.pending_only and (args.recalculate_only or args.quarter is None):
        argument_parser.error("--pending-only는 명시적 분기 재처리에만 사용할 수 있습니다")
    if args.pending_only and args.trust_previous_backfill:
        argument_parser.error("--pending-only와 --trust-previous-backfill은 함께 사용할 수 없습니다")
    if args.trust_previous_backfill and (args.recalculate_only or args.quarter is None):
        argument_parser.error("--trust-previous-backfill은 명시적 분기 백필에만 사용할 수 있습니다")
    deadline_seconds = int(os.getenv("EARNINGS_V2_DEADLINE_SECONDS", str(QUARTER_DEADLINE_SECONDS)))
    if args.year is None:
        argument_parser.error("--year가 필요합니다")
    if args.year not in {2016, 2017, 2018}:
        argument_parser.error("V2.5는 2016~2018년 백필에만 사용할 수 있습니다")
    if args.quarter and args.recalculate_only:
        with execution_deadline(deadline_seconds):
            result = pipeline.recalculate_quarter(args.year, args.quarter, write=args.write)
    elif args.quarter:
        result = pipeline.run_quarter(
            args.year, args.quarter, write=args.write,
            incremental=args.pending_only,
            discover_delistings=True,
            trust_previous_backfill=args.trust_previous_backfill,
            deadline_seconds=deadline_seconds,
        )
    else:
        # 연도 실행도 분기마다 새 마감선을 받는다. 다년 백필은 이 경계를
        # 그대로 확장해 성공한 분기 다음부터 재개할 수 있다.
        result = pipeline.run_year(
            args.year, write=args.write,
            deadline_seconds=deadline_seconds,
        )
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    if args.require_complete:
        rows = result if isinstance(result, list) else [result]
        if any(row.get("status") != "ready" for row in rows):
            sys.exit(3)
    # incomplete는 모든 기업의 처리 판단이 끝난 잠정 데이터 상태다.
    # 공급자 오류처럼 실행이 끝나지 않은 경우에는 파이프라인이 예외를
    # 발생시키고, 명시적인 failed 결과도 여기서 비정상 종료한다.
    if not args.recalculate_only and not completed_successfully(result):
        sys.exit(2)


if __name__ == "__main__":
    main()
