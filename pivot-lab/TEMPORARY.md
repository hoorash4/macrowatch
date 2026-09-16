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

Current backend flow (`major-anchor-v1`):
1. Detect major pivots for the current analysis scale.
2. Freeze those major pivots; later stages may not move, delete, or replace them.
3. Detect broader sideways zones and create only entry/exit secondary candidates.
4. For each interval between immutable major anchors, plus virtual start/end boundaries, evaluate only persistent/large deviation candidates.
5. Add secondary candidates only when they fit the interval structure; secondary candidates lose conflicts against major pivots.
6. Build the display path from virtual boundaries + immutable major pivots + accepted secondary pivots.

Removed behavior:
- No synthetic pivot creation merely to repair alternation.
- No independent direction-change rule that directly creates pivots.
- No pruning or reconstruction that can delete or replace major pivots.

Obsolete browser pivot engines were removed. The frontend does not calculate pivots.
