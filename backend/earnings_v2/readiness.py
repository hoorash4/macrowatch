from __future__ import annotations

import argparse
import os
from pathlib import Path
import re


REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")


def inspect_repository(root: Path) -> list[str]:
    errors: list[str] = []
    package = root / "backend" / "earnings_v2"
    migration_dir = root / "supabase" / "migrations"
    required_modules = (
        "models.py", "financials.py", "growth.py", "universe.py", "market.py",
        "pipeline.py", "repository.py", "sources.py", "pilot.py",
    )
    for name in required_modules:
        if not (package / name).is_file():
            errors.append(f"missing module: backend/earnings_v2/{name}")

    migrations = list(migration_dir.glob("*_create_earnings_v2_foundation.sql"))
    if len(migrations) != 1:
        errors.append("expected exactly one earnings V2 foundation migration")
    elif "earnings_v2" not in migrations[0].read_text(encoding="utf-8"):
        errors.append("earnings V2 migration does not create the isolated schema")

    # V2 must remain independently removable. Imports from the legacy package
    # are a hard readiness failure, not a style warning.
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(?:from|import)\s+(?:backend\.)?earnings(?:\.|\s|$)", text, re.MULTILINE):
            errors.append(f"legacy earnings import found: {path.name}")
    return errors


def inspect_environment() -> list[str]:
    return [f"missing environment variable: {name}" for name in REQUIRED_ENV if not os.getenv(name)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check earnings V2 readiness without fetching or writing data.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--require-environment", action="store_true")
    args = parser.parse_args()

    errors = inspect_repository(args.root)
    if args.require_environment:
        errors.extend(inspect_environment())
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("READY: earnings V2 pre-backfill structure is complete; no data was fetched or written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
