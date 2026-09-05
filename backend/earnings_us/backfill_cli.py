from __future__ import annotations

import argparse
import json

from .backfill import USEarningsBackfillPipeline


def period_range(start_year: int, start_quarter: int, end_year: int, end_quarter: int) -> list[tuple[int, int]]:
    start = start_year * 4 + start_quarter - 1
    end = end_year * 4 + end_quarter - 1
    if start < end:
        raise ValueError("start period must not be earlier than end period")
    return [(index // 4, index % 4 + 1) for index in range(start, end - 1, -1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="MacroWatch U.S. authoritative earnings backfill")
    parser.add_argument("--year", type=int)
    parser.add_argument("--quarter", type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--start-quarter", type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--end-quarter", type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--phase", choices=("universe", "earnings", "all"), default="all")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    pipeline = USEarningsBackfillPipeline.from_env()
    range_values = (args.start_year, args.start_quarter, args.end_year, args.end_quarter)
    if any(value is not None for value in range_values):
        if not all(value is not None for value in range_values):
            parser.error("all start/end year and quarter values are required for a range")
        if args.phase != "universe":
            parser.error("range execution currently supports the universe phase only")
        for year, quarter in period_range(*range_values):
            result = pipeline.freeze_universe_period(year, quarter, write=args.write)
            print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        return
    if args.year is None or args.quarter is None:
        parser.error("year and quarter are required for a single period")
    result = (
        pipeline.freeze_universe_period(args.year, args.quarter, write=args.write)
        if args.phase == "universe" else pipeline.backfill_period(args.year, args.quarter, write=args.write)
        if args.phase == "earnings" else {
            "universe": pipeline.freeze_universe_period(args.year, args.quarter, write=args.write),
            "earnings": pipeline.backfill_period(args.year, args.quarter, write=args.write),
        }
    )
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
