from __future__ import annotations

import argparse
import json

from .backfill import USEarningsBackfillPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="MacroWatch U.S. authoritative earnings backfill")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--quarter", type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(USEarningsBackfillPipeline.from_env().backfill_period(args.year, args.quarter, write=args.write), ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
