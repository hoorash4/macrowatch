import type { SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";
import { recomputePolicyScores } from "./policy-score-store.ts";
import type { PolicyReason } from "./policy-types.ts";

const ADMIN_REASONS = new Set<PolicyReason>(["inflation_fight", "growth_overheat", "recession_financial_stress", "insurance_easing"]);
type ServiceClient = SupabaseClient<any, "public", "public", any, any>;

export async function listPolicyReviews(admin: ServiceClient) {
  const { data, error } = await admin.from("central_bank_policy_events")
    .select("meeting_date,action,change_bps,ai_primary_reason,transition_assessment")
    .eq("central_bank", "fed").eq("analysis_status", "completed")
    .is("admin_primary_reason", null)
    .or("ai_primary_reason.eq.uncertain,transition_assessment.eq.confirmed")
    .order("meeting_date", { ascending: false }).limit(100);
  if (error) throw error;
  return (data || []).map((row) => ({
    meeting_date: row.meeting_date,
    action: row.action,
    change_bps: row.change_bps,
    review_type: row.ai_primary_reason === "uncertain" ? "uncertain" : "reason_transition",
  }));
}

export async function resolvePolicyReview(
  admin: ServiceClient,
  userId: string,
  body: Record<string, unknown>,
) {
  const meetingDate = String(body.meeting_date || "");
  const reason = String(body.primary_reason || "") as PolicyReason;
  const keyword = String(body.reason_keyword || "").trim();
  const score = Number(body.score);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(meetingDate)) throw new Error("회의일이 올바르지 않습니다.");
  if (!ADMIN_REASONS.has(reason)) throw new Error("정규 이유를 선택해 주세요.");
  if (!keyword || keyword.length > 80) throw new Error("이유 키워드를 80자 이내로 입력해 주세요.");
  if (!Number.isFinite(score) || score < -1_000 || score > 1_000) throw new Error("점수는 -1000부터 1000 사이로 입력해 주세요.");

  const resolvedAt = new Date().toISOString();
  const { data, error } = await admin.from("central_bank_policy_events").update({
    admin_primary_reason: reason,
    admin_reason_keyword: keyword,
    admin_score_override: score,
    admin_resolved_at: resolvedAt,
    admin_resolved_by: userId,
    primary_reason: reason,
    updated_at: resolvedAt,
  }).eq("central_bank", "fed").eq("meeting_date", meetingDate)
    .is("admin_primary_reason", null)
    .select("meeting_date").maybeSingle();
  if (error) throw error;
  if (!data) throw new Error("이미 처리되었거나 검토 대상을 찾을 수 없습니다.");
  await recomputePolicyScores(admin, "fed");
  return data;
}
