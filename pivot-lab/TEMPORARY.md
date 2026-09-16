# Pivot Lab temporary teardown

This experiment is intentionally isolated.

Remove it when the pivot logic is finalized:

1. Delete the repository folder `pivot-lab/`.
2. Delete the Supabase Edge Function `pivot-lab-engine`.
3. Drop the temporary table `public.tmp_historical_indicator_pivots` if it is still unused.

Production Historical Insight code is not part of this temporary experiment.

Current runtime split:
- Browser: chart rendering, zoom/pan, series selection, request debounce/cache only.
- Supabase Edge Function `pivot-lab-engine`: source-series query plus major-pivot, sideways-zone, deviation, alternation-recovery, convergence/pruning calculations.
