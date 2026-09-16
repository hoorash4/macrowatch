# Pivot Lab temporary teardown

This experiment is intentionally isolated.

Remove it when the pivot logic is finalized:

1. Delete the repository folder `pivot-lab/`.
2. Delete the Supabase Edge Function `pivot-lab-engine` if it is still present; the current Pivot Lab no longer calls it.
3. Drop the temporary table `public.tmp_historical_indicator_pivots` if it is still unused.

Production Historical Insight code is not part of this temporary experiment.

Current runtime split:
- Browser: authenticated source-series query, one fixed-scale pivot calculation, chart rendering, zoom/pan.
- `pivot-lab/pivot-engine.js`: all pivot logic.
- Zoom/pan never triggers recalculation. They are display-only operations.
- Supabase Edge Function `pivot-lab-engine` is currently unused by the page.

Current engine flow (`frontend-major-anchor-v1`):
1. Treat the selected series' full loaded range as one fixed analysis scale.
2. Reduce only the calculation working set with scale-preserving min/max sampling when needed; keep the raw series for display and final pivot snapping.
3. Detect major pivots and freeze them.
4. Detect broader sideways zones and create entry/exit secondary candidates.
5. For each interval between immutable major anchors, plus virtual start/end boundaries, evaluate only persistent/large deviation candidates.
6. Add secondary candidates only when they fit the interval structure; secondary candidates lose conflicts against major pivots.
7. Snap final HIGH/LOW positions back to actual extrema in the raw series.
8. Build the display path from virtual boundaries + immutable major pivots + accepted secondary pivots.

Removed behavior:
- No zoom/pan-triggered pivot recalculation.
- No Edge Function dependency for Pivot Lab calculation.
- No synthetic pivot creation merely to repair alternation.
- No independent direction-change rule that directly creates pivots.
- No pruning or reconstruction that can delete or replace major pivots.
