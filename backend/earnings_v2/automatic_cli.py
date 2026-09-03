from __future__ import annotations

import argparse
import json
import os

from .automatic import KoreaEarningsV2AutomaticPipeline


DAILY_DEADLINE_SECONDS = 240


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="MacroWatch Earnings V2 Korean automatic filing collection",
    )
    result.add_argument(
        "--write",
        action="store_true",
        help="신규 공시·대기 기업과 잠정 또는 확정 집계를 V2 DB에 저장",
    )
    result.add_argument(
        "--phase",
        choices=("dart", "kis", "all"),
        default="all",
        help="dart=19:30 공시 수집, kis=20:30 대기열 보완, all=수동 연속 검증",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    deadline_seconds = int(
        os.getenv("EARNINGS_V2_AUTOMATIC_DEADLINE_SECONDS", str(DAILY_DEADLINE_SECONDS)),
    )
    pipeline = KoreaEarningsV2AutomaticPipeline.from_env()
    if args.phase == "dart":
        result = pipeline.run_daily(write=args.write, deadline_seconds=deadline_seconds)
    elif args.phase == "kis":
        result = pipeline.run_kis_pending(write=args.write, deadline_seconds=deadline_seconds)
    else:
        result = {
            "dart": pipeline.run_daily(write=args.write, deadline_seconds=deadline_seconds),
            "kis": pipeline.run_kis_pending(write=args.write, deadline_seconds=deadline_seconds),
        }
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
