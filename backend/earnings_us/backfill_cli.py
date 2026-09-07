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


def chronological_period_range(start_year: int, start_quarter: int, end_year: int, end_quarter: int) -> list[tuple[int, int]]:
    start = start_year * 4 + start_quarter - 1
    end = end_year * 4 + end_quarter - 1
    if start > end:
        raise ValueError("start period must not be later than end period")
    return [(index // 4, index % 4 + 1) for index in range(start, end + 1)]


def _period_label(period: tuple[int, int]) -> str:
    return f"{period[0]}Q{period[1]}"


def resumable_periods(
    periods: list[tuple[int, int]], state: dict | None,
    range_start: str, range_end: str,
) -> tuple[list[tuple[int, int]], tuple[int, int] | None]:
    """Resume only a checkpoint created for this exact requested range."""
    if not periods or not isinstance(state, dict):
        return periods, None
    cursor = state.get("cursor")
    if not isinstance(cursor, dict):
        return periods, None
    if cursor.get("range_start") != range_start or cursor.get("range_end") != range_end:
        return periods, None

    labels = [_period_label(period) for period in periods]
    interrupted = None
    checkpoint = None
    if state.get("status") == "running":
        checkpoint = cursor.get("current_period")
        interrupted = periods[labels.index(checkpoint)] if checkpoint in labels else None
    elif state.get("status") == "failed":
        checkpoint = cursor.get("failed_period")
    elif cursor.get("range_complete"):
        return [], None
    else:
        completed = cursor.get("last_completed_period")
        if completed in labels:
            return periods[labels.index(completed) + 1:], None

    if checkpoint in labels:
        return periods[labels.index(checkpoint):], interrupted
    return periods, None


def run_earnings_range(
    pipeline, periods: list[tuple[int, int]], *, write: bool, force_rerun: bool = False,
) -> dict:
    if not periods:
        return {"status": "ready", "processed_periods": 0, "already_complete": True}
    range_start = _period_label(periods[0])
    range_end = _period_label(periods[-1])
    state = pipeline.repository.us_state("backfill_range") if write and not force_rerun else None
    pending, interrupted = resumable_periods(periods, state, range_start, range_end)
    if not pending:
        return {"status": "ready", "processed_periods": 0, "already_complete": True}

    if interrupted is not None:
        pipeline.repository.clear_us_backfill_period(*interrupted)

    processed = 0
    final_status = "ready"
    for year, quarter in pending:
        period = f"{year}Q{quarter}"
        checkpoint = {"range_start": range_start, "range_end": range_end, "current_period": period}
        try:
            if write:
                pipeline.repository.save_us_state("backfill_range", "running", checkpoint)
            result = pipeline.backfill_period(
                year, quarter, write=write, strict_provider_errors=True,
            )
            print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
            processed += 1
            final_status = result["status"]
            if write:
                pipeline.repository.save_us_state("backfill_range", result["status"], {
                    "range_start": range_start,
                    "range_end": range_end,
                    "last_completed_period": period,
                    "range_complete": (year, quarter) == periods[-1],
                })
        except Exception as exc:
            cleanup = {}
            cleanup_error = None
            state_error = None
            if write:
                try:
                    cleanup = pipeline.repository.clear_us_backfill_period(year, quarter)
                except Exception as cleanup_exc:
                    cleanup_error = str(cleanup_exc)
                error = str(exc) if cleanup_error is None else f"{exc}; cleanup failed: {cleanup_error}"
                try:
                    pipeline.repository.save_us_state("backfill_range", "failed", {
                        "range_start": range_start,
                        "range_end": range_end,
                        "failed_period": period,
                        "cleanup": cleanup,
                    }, error)
                except Exception as state_exc:
                    state_error = str(state_exc)
            else:
                error = str(exc)
            if state_error is not None:
                error = f"{error}; failure checkpoint save failed: {state_error}"
            raise RuntimeError(f"U.S. backfill stopped at {period}: {error}") from exc
    return {"status": final_status, "processed_periods": processed, "already_complete": False}


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
    parser.add_argument("--force-rerun", action="store_true")
    args = parser.parse_args()
    pipeline = USEarningsBackfillPipeline.from_env()
    range_values = (args.start_year, args.start_quarter, args.end_year, args.end_quarter)
    if any(value is not None for value in range_values):
        if not all(value is not None for value in range_values):
            parser.error("all start/end year and quarter values are required for a range")
        periods = (
            period_range(*range_values)
            if args.phase == "universe"
            else chronological_period_range(*range_values)
            if args.phase == "earnings"
            else parser.error("range execution requires either universe or earnings phase")
        )
        if args.phase == "earnings":
            print(json.dumps(run_earnings_range(
                pipeline, periods, write=args.write, force_rerun=args.force_rerun,
            ), ensure_ascii=False), flush=True)
            return
        for year, quarter in periods:
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

