"""Idempotently queue the fixed 2002-2015 DART archive backfill."""

from __future__ import annotations

import json

from earnings.supabase_rest import SupabaseEarningsStore


def main() -> None:
    store = SupabaseEarningsStore.from_env()
    inserted = store.enqueue_open_dart_legacy_backfill()
    requeued = store.requeue_unvalidated_legacy_jobs()
    parser_v2_repair = store.enqueue_legacy_2015_parser_v2_repair()
    print(json.dumps({
        "ok": True, "inserted": inserted, "requeued_for_quality": requeued,
        "queued_2015_parser_v2_repair": parser_v2_repair,
        "start_year": 2002, "end_year": 2015,
    }))


if __name__ == "__main__":
    main()
