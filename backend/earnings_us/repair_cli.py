from __future__ import annotations

import argparse
import json

from .pipeline import USEarningsAutomaticPipeline


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="MacroWatch U.S. earnings manual repair for incomplete stored rows",
    )
    result.add_argument("--write", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    pipeline = USEarningsAutomaticPipeline.from_env()
    result = pipeline.retry_incomplete(write=args.write)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
