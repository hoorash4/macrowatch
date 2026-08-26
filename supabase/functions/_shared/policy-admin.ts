import type { SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";
import { recomputePolicyScores } from "./policy-score-store.ts";
import type { PolicyReason } from "./policy-types.ts";

const ADMIN_REASONS = new Set<PolicyReason>(["inflation_fight", "growth_overheat", "recession_financial_stress", "insurance_easing", "normalization_hike", "normalization_cut", "uncertain"]);
type ServiceClient = SupabaseClient<any, "public", "public", any, any>;

export async function listPolicyReviews(admin: ServiceClient) {
  const fields = "meeting_date,action,change_bps,ai_primary_reason,primary_reason,transition_assessment,admin_primary_reason,admin_reason_keyword,admin_score_override,final_event_score";
  const [{ data: unresolved, error: unresolvedError }, { data: latest, error: latestError }] = await Promise.all([
    admin.from("central_bank_policy_events")
      .select(fields)
    .eq("central_bank", "fed").eq("analysis_status", "completed")
    .neq("action", "hold")
    .is("admin_primary_reason", null)
    .or("ai_primary_reason.eq.uncertain,transition_assessment.eq.confirmed")
      .order("meeting_date", { ascending: false }).limit(100),
    admin.from("central_bank_policy_events")
      .select(fields).eq("central_bank", "fed").eq("analysis_status", "completed")
      .order("meeting_date", { ascending: false }).limit(1).maybeSingle(),
  ]);
  if (unresolvedError) throw unresolvedError;
  if (latestError) throw latestError;
  const latestDate = latest?.meeting_date;
  const rows = [...(latest ? [latest] : []), ...(unresolved || []).filter((row) => row.meeting_date !== latestDate)];
  return rows.map((row) => ({
    meeting_date: row.meeting_date,
    action: row.action,
    change_bps: row.change_bps,
    review_type: row.meeting_date === latestDate ? "latest" : row.ai_primary_reason === "uncertain" ? "uncertain" : "reason_transition",
    primary_reason: row.admin_primary_reason || row.primary_reason || row.ai_primary_reason,
    reason_keyword: row.admin_reason_keyword || "",
    score: row.admin_score_override ?? row.final_event_score,
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
  const rawScore = body.score;
  const score = Number(body.score);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(meetingDate)) throw new Error("회의일이 올바르지 않습니다.");
  if (!ADMIN_REASONS.has(reason)) throw new Error("정규 이유를 선택해 주세요.");
  const { data: current, error: currentError } = await admin.from("central_bank_policy_events")
    .select("meeting_date,action,ai_primary_reason,transition_assessment,admin_primary_reason")
    .eq("central_bank", "fed").eq("meeting_date", meetingDate).eq("analysis_status", "completed").maybeSingle();
  if (currentError) throw currentError;
  if (!current) throw new Error("검토할 FOMC 결과를 찾을 수 없습니다.");
  const { data: latest, error: latestError } = await admin.from("central_bank_policy_events")
    .select("meeting_date").eq("central_bank", "fed").eq("analysis_status", "completed")
    .order("meeting_date", { ascending: false }).limit(1).maybeSingle();
  if (latestError) throw latestError;
  const isLatest = latest?.meeting_date === meetingDate;
  const isUnresolvedReview = current.action !== "hold" && !current.admin_primary_reason
    && (current.ai_primary_reason === "uncertain" || current.transition_assessment === "confirmed");
  if (!isLatest && !isUnresolvedReview) throw new Error("현재 수정할 수 있는 검토 대상이 아닙니다.");
  // Manual review owns the final reason and score, so it is not constrained by
  // the action-to-reason direction rules used to validate automated analysis.
  if (keyword.length > 80) throw new Error("이유 키워드는 80자 이내로 입력해 주세요.");
  if (rawScore === null || rawScore === undefined || String(rawScore).trim() === "" || !Number.isFinite(score) || score < -1_000 || score > 1_000) {
    throw new Error("점수는 0을 포함해 -1000부터 1000 사이의 숫자로 직접 입력해 주세요.");
  }

  const resolvedAt = new Date().toISOString();
  const { data, error } = await admin.from("central_bank_policy_events").update({
    admin_primary_reason: reason,
    admin_reason_keyword: keyword || null,
    admin_score_override: score,
    admin_resolved_at: resolvedAt,
    admin_resolved_by: userId,
    primary_reason: reason,
    updated_at: resolvedAt,
  }).eq("central_bank", "fed").eq("meeting_date", meetingDate)
    .select("meeting_date").maybeSingle();
  if (error) throw error;
  if (!data) throw new Error("이미 처리되었거나 검토 대상을 찾을 수 없습니다.");
  await recomputePolicyScores(admin, "fed");
  return data;
}
