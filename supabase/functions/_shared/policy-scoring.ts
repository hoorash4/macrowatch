import type { PolicyAction, PolicyReason, PolicyScoringInput, PolicyScoringResult, PolicyTrendType } from "./policy-types.ts";

export const POLICY_INDEX_BASE = 1_000;
export const POLICY_SCORE_PROFILE = "fed-policy-v3";

type DirectionState = {
  action: Exclude<PolicyAction, "hold">;
  segment: number;
  sequence: number;
  hasBridge: boolean;
  holds: number;
  terminationStarted: boolean;
  reason: PolicyReason;
  reasonSegment: number;
  reasonSequence: number;
};

const round = (value: number) => Number(value.toFixed(3));
const directional = (action: PolicyAction): action is Exclude<PolicyAction, "hold"> => action !== "hold";
const largeMoveWeight = (changeBps: number | null | undefined) => {
  const extraSteps = Math.floor(Math.max(0, Math.abs(Number(changeBps || 0)) - 25) / 25);
  return extraSteps * 0.25;
};

function rawBase(reason: PolicyReason, action: Exclude<PolicyAction, "hold">) {
  if (reason === "inflation_fight" && action === "hike") return 100;
  if (reason === "growth_overheat" && action === "hike") return -50;
  if (reason === "recession_financial_stress" && action === "cut") return 100;
  if (reason === "insurance_easing" && action === "cut") return -50;
  if (reason === "normalization_hike" && action === "hike") return -50;
  if (reason === "normalization_cut" && action === "cut") return -50;
  return 0;
}

function directionalScore(reason: PolicyReason, action: Exclude<PolicyAction, "hold">, reasonSequence: number) {
  const raw = rawBase(reason, action);
  if (!raw) return 0;
  if (reason === "insurance_easing" && action === "cut") {
    if (reasonSequence === 1) return -50;
    if (reasonSequence === 2) return -25;
    return 0;
  }
  return round(raw / reasonSequence);
}

function holdDirection(reason: PolicyReason) {
  if (reason === "inflation_fight" || reason === "recession_financial_stress") return -1;
  if (reason === "growth_overheat") return 1;
  return 0;
}

function trendType(state: DirectionState): PolicyTrendType {
  if (state.holds) return state.terminationStarted ? "hold_scoring" : "hold_pending";
  if (state.sequence >= 3) return state.hasBridge ? "bridge_confirmed" : "confirmed";
  if (state.sequence === 2) return state.hasBridge ? "bridge_pending" : "adjustment";
  return "single";
}

export function scorePolicyHistory(inputRows: PolicyScoringInput[]): PolicyScoringResult[] {
  const rows = [...inputRows].sort((left, right) => left.meeting_date.localeCompare(right.meeting_date));
  let directionSegment = 0;
  let reasonSegment = 0;
  let state: DirectionState | null = null;
  let policyIndex = POLICY_INDEX_BASE;
  let rateCycleId = 0;
  let lastDirectionalAction: Exclude<PolicyAction, "hold"> | null = null;
  let peakCandidate: { upper: number; formedDate: string; resultIndex: number } | null = null;
  let previousPeak: { upper: number; formedDate: string } | null = null;
  let previousPeakReachedThisCycle = false;
  const results: PolicyScoringResult[] = [];

  rows.forEach((row) => {
    const reason = row.admin_primary_reason || row.ai_primary_reason;
    const largeMoveMultiplier = largeMoveWeight(row.change_bps);
    const hasLargeRateMove = largeMoveMultiplier > 0;
    let baseScore = 0;
    let firstAdjustment = 0;
    let largeAdjustment = 0;
    let emergencyAdjustment = 0;
    let holdAdjustment = 0;
    let previousPeakAdjustment = 0;
    let previousPeakAgeDays: number | null = null;
    let previousPeakReached = false;

    if (row.action === "hike") {
      if (lastDirectionalAction !== "hike") {
        rateCycleId += 1;
        peakCandidate = null;
        previousPeakReachedThisCycle = false;
      }
      if (previousPeak && typeof row.target_range_upper === "number" && !previousPeakReachedThisCycle) {
        previousPeakReached = row.target_range_upper >= previousPeak.upper;
        if (previousPeakReached) {
          previousPeakReachedThisCycle = true;
          previousPeakAgeDays = Math.floor((Date.parse(row.meeting_date) - Date.parse(previousPeak.formedDate)) / 86_400_000);
          if (previousPeakAgeDays >= 360) previousPeakAdjustment = 100;
        }
      }
      if (typeof row.target_range_upper === "number" && (!peakCandidate || row.target_range_upper > peakCandidate.upper)) {
        peakCandidate = { upper: row.target_range_upper, formedDate: row.meeting_date, resultIndex: results.length };
      }
      lastDirectionalAction = "hike";
    } else if (row.action === "cut") {
      if (lastDirectionalAction === "hike" && peakCandidate) {
        previousPeak = { upper: peakCandidate.upper, formedDate: peakCandidate.formedDate };
        const peakRow = results[peakCandidate.resultIndex];
        if (peakRow) {
          peakRow.is_confirmed_rate_peak = true;
          peakRow.rate_peak_upper = peakCandidate.upper;
          peakRow.rate_peak_formed_date = peakCandidate.formedDate;
        }
      }
      peakCandidate = null;
      previousPeakReachedThisCycle = false;
      lastDirectionalAction = "cut";
    }

    if (directional(row.action)) {
      const startsNewDirection = !state
        || state.action !== row.action
        || state.terminationStarted
        || state.holds >= 2;
      if (startsNewDirection) {
        directionSegment += 1;
        reasonSegment += 1;
        state = { action: row.action, segment: directionSegment, sequence: 1, hasBridge: false, holds: 0, terminationStarted: false, reason, reasonSegment, reasonSequence: 1 };
      } else {
        const continuingState = state as DirectionState;
        if (continuingState.holds === 1) continuingState.hasBridge = true;
        continuingState.sequence += 1;
        continuingState.holds = 0;
        if (continuingState.reason === reason) continuingState.reasonSequence += 1;
        else {
          reasonSegment += 1;
          continuingState.reason = reason;
          continuingState.reasonSegment = reasonSegment;
          continuingState.reasonSequence = 1;
        }
      }

      const activeState = state as DirectionState;
      baseScore = directionalScore(reason, row.action, activeState.reasonSequence);
      const rawMagnitude = Math.abs(rawBase(reason, row.action));
      const adjustmentMagnitude = activeState.reasonSequence > 0 ? rawMagnitude / activeState.reasonSequence : 0;
      const firstEligible = activeState.sequence === 1
        && ((reason === "inflation_fight" && row.action === "hike")
          || (reason === "recession_financial_stress" && row.action === "cut"));
      firstAdjustment = firstEligible ? round(adjustmentMagnitude * 0.10) : 0;
      if (baseScore !== 0) {
        largeAdjustment = round(adjustmentMagnitude * largeMoveMultiplier);
        emergencyAdjustment = row.is_emergency ? round(adjustmentMagnitude * 0.50) : 0;
      }
    } else if (state) {
      state.holds += 1;
      const requiredDirectionCount = state.sequence >= 3 ? 3 : state.sequence;
      const triggerHold = state.hasBridge ? 2 : 1;
      const eligible = requiredDirectionCount >= 2 && state.holds >= triggerHold;
      if (eligible) {
        state.terminationStarted = true;
        const startScore = state.sequence >= 3 ? 100 : 50;
        const holdScoreSequence = state.holds - triggerHold + 1;
        // An uncertain hold still advances the factual hold sequence, but its
        // policy meaning is intentionally left pending and always scores zero.
        holdAdjustment = reason === "uncertain"
          ? 0
          : round((holdDirection(state.reason) * startScore) / holdScoreSequence);
      } else if (state.sequence < 2 && state.holds >= 2) {
        state.terminationStarted = true;
      }
    }

    const automaticScore = round(baseScore + firstAdjustment + largeAdjustment + emergencyAdjustment + holdAdjustment + previousPeakAdjustment);
    const hasAdminScore = typeof row.admin_score_override === "number" && Number.isFinite(row.admin_score_override);
    const finalEventScore = hasAdminScore ? row.admin_score_override! : automaticScore;
    policyIndex = round(policyIndex + finalEventScore);
    const result: PolicyScoringResult = {
      ...row,
      effective_reason: reason,
      direction_segment: state?.segment ?? null,
      direction_sequence: state?.sequence ?? 0,
      reason_segment: state?.reasonSegment ?? null,
      reason_sequence: state?.reasonSequence ?? 0,
      trend_type: state ? trendType(state) : "none",
      hold_sequence: state?.holds ?? 0,
      base_score: baseScore,
      first_decision_adjustment: firstAdjustment,
      large_move_adjustment: largeAdjustment,
      emergency_adjustment: emergencyAdjustment,
      hold_adjustment: holdAdjustment,
      previous_peak_adjustment: previousPeakAdjustment,
      rate_cycle_id: rateCycleId || null,
      is_confirmed_rate_peak: false,
      rate_peak_upper: null,
      rate_peak_formed_date: null,
      previous_peak_upper: previousPeak?.upper ?? null,
      previous_peak_formed_date: previousPeak?.formedDate ?? null,
      previous_peak_age_days: previousPeakAgeDays,
      previous_peak_reached: previousPeakReached,
      final_event_score: round(finalEventScore),
      policy_index: policyIndex,
      has_large_rate_move: hasLargeRateMove,
    };
    results.push(result);
  });
  return results;
}
