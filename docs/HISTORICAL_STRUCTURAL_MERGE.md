# Historical pivot structural merge experiment

Rollback baseline: `ab8a32142467b2b25e38983d42cec0c53262ca58`.

## Select or undo

`simplify_pivot_lines(augmented, geometry, merge_method="structural")` uses the new
default post-pass. `merge_method="angle"` selects the unchanged old angle pass;
`merge_method="none"` returns the connected path before either post-pass.
Changing `DEFAULT_TREND_MERGE_METHOD` to `"angle"` restores the old default.
The implementation commit can also be reverted as one unit. Source data and
base envelope/RDP pivots are never modified.

## Rule and scope

The new retrospective post-pass consumes the existing connected pivot polyline,
not raw observations. It holds the starting low/high fixed and evaluates later
record highs/lows. For every intervening correction, it checks:

- retracement / advance before that correction: at most 0.5;
- peak-to-recovery duration / whole proposed trend duration: at most 0.5;
- peak-to-recovery duration / preceding advance duration: at most 1.0;
- progress beyond the former record / preceding advance: at least 0.1.

These are provisional dimensionless defaults in `StructuralMergePolicy`, not
empirically calibrated constants. Deep reversals cannot be washed out by a much
larger future high/low: the depth denominator is fixed at the preceding advance.
Recovery uses the first surviving endpoint that regains the prior record. Its
date is a conservative recovered-by date, not the exact raw-series crossing.
No fixed daily/weekly/monthly observation count or specific date/series code is
used. Pure directional continuation can be collapsed without a correction.

Every accepted interval replaces its connected segments with one trend segment.
Ordinary and spike interiors can be absorbed after the same checks. Sideways spans
(from segment kind or sideways metadata) and both endpoints are protected: merges
stop at these boundaries, and the original sideways records are preserved.
Marker-only spikes are included in validation. Disconnected paths are not joined.
Diagnostics report accepted/rejected candidates, ratios, reasons and selected
intervals in `merge_diagnostics`. `accepted` means eligible; `selected` identifies
the interval actually used. Original input objects remain intact for comparison.

Only this post-pass is geometry independent. Earlier envelope/RDP selection and
screen-angle-based sideways/spike classification are unchanged. This is not an
online detector and must not be used as if its retrospective decisions were
available in real time.

## Invocation and validation

Existing Python graph callers of `simplify_pivot_lines` use the structural default.
The module's existing CLI still outputs base marker JSON; it does not plot images
or call the simplified-line stage. Browser JS and database paths are unchanged.

Run `python -m unittest discover -s tests -p test_historical_structural_merge.py -v`.
The baseline `test_historical_pivot_base.py` already fails 9 cases before this
change: six missing test imports and three old connection-rule assertions.
Those failures are not evidence that this experiment passed the whole suite.
Actual US10Y2Y GFC image reproduction still requires the plotting caller and its
exact input data/geometry, which are not present in this repository.
