# Canonical economic-series contract

Reusable economic and market series have one read contract:
`public.economic_chart_series_points`, keyed by `(series_code, observation_date)`.

- Externally published observations live in `public.economic_chart_points`.
- Reusable calculations and resampled observations live in
  `public.economic_chart_derived_points`.
- Published Cleveland Fed CPI/PCE nowcast vintages live in
  `public.inflation_nowcast_vintages` because they require both a target month
  and an observation date.

## Write boundary

- Source collectors write through `backend/signals/canonical_series.py::store`.
- Calculators write reusable chart series through
  `backend/signals/derived_series.py::store` and read their inputs from stored
  canonical observations.
- Edge Functions that need an atomic multi-table write use a narrowly scoped, service-role-only RPC.
- Browser and alert code read `economic_chart_series_points` and never write it.
- Feature and model tables may store derived results and calculation metadata. They must not copy reusable source observations into feature-specific columns or tables.

## Change rule

A new collector must reuse an existing `series_code` when the underlying observation is the same. A new raw/source/cache/observation table requires an explicit architectural exception and a corresponding contract-test update. Migration-only changes run the structure workflow, so this rule is checked before deployment.

Scheduled workflows run source jobs first and DB-only derived jobs second. Provider
credentials are absent from derived jobs. Historical backfill and scheduled automatic
collection may share source adapters and the source storage boundary, but their
execution paths and replacement policies remain separate.
