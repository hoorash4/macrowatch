import type { SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";
import { POLICY_SCORE_PROFILE, scorePolicyHistory } from "./policy-scoring.ts";
import type { PolicyScoringInput } from "./policy-types.ts";

type ServiceClient = SupabaseClient<any, "public", "public", any, any>;

export async function recomputePolicyScores(supabase: ServiceClient, centralBank = "fed") {
  const { data, error } = await supabase.from("central_bank_policy_events")
    .select("central_bank,meeting_date,action,ai_primary_reason,primary_reason,admin_primary_reason,admin_score_override,change_bps,is_emergency")
    .eq("central_bank", centralBank).eq("analysis_status", "completed").order("meeting_date");
  if (error) throw error;
  const inputs = (data || []).filter((row) => row.action && (row.ai_primary_reason || row.primary_reason)).map((row) => ({
    ...row,
    ai_primary_reason: row.ai_primary_reason || row.primary_reason,
  })) as PolicyScoringInput[];
  const scored = scorePolicyHistory(inputs);
  for (const row of scored) {
    const { error: updateError } = await supabase.from("central_bank_policy_events").update({
      primary_reason: row.effective_reason,
      direction_segment: row.direction_segment,
      direction_sequence: row.direction_sequence,
      reason_segment: row.reason_segment,
      reason_sequence: row.reason_sequence,
      trend_type: row.trend_type,
      hold_sequence: row.hold_sequence,
      base_score: row.base_score,
      first_decision_adjustment: row.first_decision_adjustment,
      large_move_adjustment: row.large_move_adjustment,
      emergency_adjustment: row.emergency_adjustment,
      hold_adjustment: row.hold_adjustment,
      final_event_score: row.final_event_score,
      policy_index: row.policy_index,
      has_large_rate_move: row.has_large_rate_move,
      score_profile_version: POLICY_SCORE_PROFILE,
      updated_at: new Date().toISOString(),
    }).eq("central_bank", centralBank).eq("meeting_date", row.meeting_date);
    if (updateError) throw updateError;
  }
  return scored;
}
