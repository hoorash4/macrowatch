"""Idempotently queue the fixed 2002-2015 DART archive backfill."""

from __future__ import annotations

import json

from earnings.supabase_rest import SupabaseEarningsStore


def main() -> None:
    store = SupabaseEarningsStore.from_env()
    inserted = store.enqueue_open_dart_legacy_backfill()
    requeued = store.requeue_unvalidated_legacy_jobs()
    print(json.dumps({
        "ok": True, "inserted": inserted, "requeued_for_quality": requeued,
        "start_year": 2002, "end_year": 2015,
    }))


if __name__ == "__main__":
    main()
