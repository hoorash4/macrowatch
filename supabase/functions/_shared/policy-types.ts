export type PolicyAction = "hike" | "hold" | "cut";
export type PolicyReason = "inflation_fight" | "growth_overheat" | "recession_financial_stress" | "insurance_easing" | "uncertain";
export type PolicyTrendType = "none" | "single" | "adjustment" | "confirmed" | "bridge_pending" | "bridge_confirmed" | "hold_pending" | "hold_scoring";

export type PolicyScoringInput = {
  central_bank: string;
  meeting_date: string;
  action: PolicyAction;
  ai_primary_reason: PolicyReason;
  admin_primary_reason?: PolicyReason | null;
  admin_score_override?: number | null;
  change_bps?: number | null;
  is_emergency?: boolean;
};

export type PolicyScoringResult = PolicyScoringInput & {
  effective_reason: PolicyReason;
  direction_segment: number | null;
  direction_sequence: number;
  reason_segment: number | null;
  reason_sequence: number;
  trend_type: PolicyTrendType;
  hold_sequence: number;
  base_score: number;
  first_decision_adjustment: number;
  large_move_adjustment: number;
  emergency_adjustment: number;
  hold_adjustment: number;
  final_event_score: number;
  policy_index: number;
  has_large_rate_move: boolean;
};
