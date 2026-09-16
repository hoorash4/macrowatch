# Pivot Lab temporary teardown

This experiment is intentionally isolated.

Remove it when the pivot logic is finalized:

1. Delete the repository folder `pivot-lab/`.
2. Delete the Supabase Edge Function `pivot-lab-engine`.
3. Drop the temporary table `public.tmp_historical_indicator_pivots` if it is still unused.

Production Historical Insight code is not part of this temporary experiment.

Current runtime split:
- Browser: chart rendering, zoom/pan, series selection, request debounce/cache only.
- Supabase Edge Function `pivot-lab-engine`: source-series query and all pivot calculations.

Current backend flow (`explainability-v1`):
1. Major skeleton
2. Segment explainability check: can one straight line still explain the segment?
3. Split only when there is a persistent direction change or persistent/very large deviation
4. Preserve confirmed sideways entry/exit boundaries as structural pivots
5. Rebuild HIGH/LOW alternation
6. Final prune removes a pivot only when the merged outer segment is explainable
7. Re-run explainability after pruning so every final adjacent segment satisfies the same rule

Obsolete browser pivot engines were removed. The frontend does not calculate pivots.