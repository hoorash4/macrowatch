from __future__ import annotations

import argparse
import json

from .automatic import KoreaEarningsV2AutomaticPipeline


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Recalculate one stored Korean earnings quarter",
    )
    result.add_argument("--year", type=int, required=True)
    result.add_argument("--quarter", type=int, choices=(1, 2, 3, 4), required=True)
    result.add_argument("--write", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    pipeline = KoreaEarningsV2AutomaticPipeline.from_env()
    result = pipeline.recalculate_quarter(args.year, args.quarter, write=args.write)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
