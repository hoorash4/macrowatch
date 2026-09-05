from __future__ import annotations

import argparse
import json

from .pipeline import USEarningsAutomaticPipeline


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="MacroWatch U.S. automatic earnings collection")
    result.add_argument("--write", action="store_true", help="Persist the U.S. universe and SEC facts")
    result.add_argument("--phase", choices=("snapshot", "edgar", "all"), default="all")
    return result


def main() -> None:
    args = parser().parse_args()
    pipeline = USEarningsAutomaticPipeline.from_env()
    result = {"snapshot": pipeline.snapshot(write=args.write), "edgar": pipeline.daily_edgar(write=args.write)} if args.phase == "all" else (
        pipeline.snapshot(write=args.write) if args.phase == "snapshot" else pipeline.daily_edgar(write=args.write)
    )
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
