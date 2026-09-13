# Canonical economic-series contract

Reusable economic and market observations have one canonical database home:
`public.economic_chart_points`, keyed by `(series_code, observation_date)`.

## Write boundary

- Python collectors write through `backend/signals/canonical_series.py::store`.
- Edge Functions that need an atomic multi-table write use a narrowly scoped, service-role-only RPC.
- Browser code reads canonical observations and never writes them.
- Feature and model tables may store derived results and calculation metadata. They must not copy reusable source observations into feature-specific columns or tables.

## Change rule

A new collector must reuse an existing `series_code` when the underlying observation is the same. A new raw/source/cache/observation table requires an explicit architectural exception and a corresponding contract-test update. Migration-only changes run the structure workflow, so this rule is checked before deployment.

Historical backfill and scheduled automatic collection may share source adapters and this storage boundary, but their execution paths and replacement policies remain separate.
