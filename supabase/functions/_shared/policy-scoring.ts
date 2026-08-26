import type { PolicyAction, PolicyReason, PolicyScoringInput, PolicyScoringResult, PolicyTrendType } from "./policy-types.ts";

export const POLICY_INDEX_BASE = 1_000;
export const POLICY_SCORE_PROFILE = "fed-policy-v2";

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

function rawBase(reason: PolicyReason, action: Exclude<PolicyAction, "hold">) {
  if (reason === "inflation_fight" && action === "hike") return 100;
  if (reason === "growth_overheat" && action === "hike") return -50;
  if (reason === "recession_financial_stress" && action === "cut") return 100;
  if (reason === "insurance_easing" && action === "cut") return -50;
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

  return rows.map((row) => {
    const reason = row.admin_primary_reason || row.ai_primary_reason;
    const hasLargeRateMove = Math.abs(Number(row.change_bps || 0)) >= 50;
    let baseScore = 0;
    let firstAdjustment = 0;
    let largeAdjustment = 0;
    let emergencyAdjustment = 0;
    let holdAdjustment = 0;

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
        if (state.holds === 1) state.hasBridge = true;
        state.sequence += 1;
        state.holds = 0;
        if (state.reason === reason) state.reasonSequence += 1;
        else {
          reasonSegment += 1;
          state.reason = reason;
          state.reasonSegment = reasonSegment;
          state.reasonSequence = 1;
        }
      }

      const activeState = state;
      baseScore = directionalScore(reason, row.action, activeState.reasonSequence);
      const rawMagnitude = Math.abs(rawBase(reason, row.action));
      const adjustmentMagnitude = activeState.reasonSequence > 0 ? rawMagnitude / activeState.reasonSequence : 0;
      const firstEligible = activeState.sequence === 1
        && ((reason === "inflation_fight" && row.action === "hike")
          || (reason === "recession_financial_stress" && row.action === "cut"));
      firstAdjustment = firstEligible ? round(adjustmentMagnitude * 0.10) : 0;
      if (baseScore !== 0) {
        largeAdjustment = hasLargeRateMove ? round(adjustmentMagnitude * 0.25) : 0;
        emergencyAdjustment = row.is_emergency ? round(adjustmentMagnitude * 0.25) : 0;
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
        holdAdjustment = round((holdDirection(state.reason) * startScore) / holdScoreSequence);
      } else if (state.sequence < 2 && state.holds >= 2) {
        state.terminationStarted = true;
      }
    }

    const automaticScore = round(baseScore + firstAdjustment + largeAdjustment + emergencyAdjustment + holdAdjustment);
    const hasAdminScore = typeof row.admin_score_override === "number" && Number.isFinite(row.admin_score_override);
    const finalEventScore = hasAdminScore ? row.admin_score_override! : automaticScore;
    policyIndex = round(policyIndex + finalEventScore);
    return {
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
      final_event_score: round(finalEventScore),
      policy_index: policyIndex,
      has_large_rate_move: hasLargeRateMove,
    };
  });
}
