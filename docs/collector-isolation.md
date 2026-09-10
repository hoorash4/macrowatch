# Automatic collection isolation contract

Production automatic collection has one responsibility: collect and publish data for its own current release window. It must not use routine collection as an implicit backfill, historical rebuild, retention cleanup, schema replay, or unrelated deployment trigger.

## Required boundaries

- Existing confirmed historical rows are immutable during automatic collection.
- A lookback window may be used to calculate a current value, but it does not authorize rewriting that lookback history.
- Provisional/current rows may be completed or replaced only where the collector explicitly models the active cycle as mutable.
- Historical rebuild, repair, and destructive retention are explicit maintenance operations and are never side effects of a scheduled collector.
- Backend-only changes do not deploy GitHub Pages.
- A Supabase change deploys only newly added migrations and the Edge Functions actually affected by the change. Existing migrations are immutable and are not replayed on every deployment.
- A shared Edge Function dependency change may redeploy all functions that consume that shared code; this is considered a direct dependency, not an unrelated deployment.

The regression tests in `tests/test_collector_isolation.py` enforce the highest-risk structural boundaries.
