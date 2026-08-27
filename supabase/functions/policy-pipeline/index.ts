import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { recomputePolicyScores } from "../_shared/policy-score-store.ts";
import { POLICY_SCORE_PROFILE } from "../_shared/policy-scoring.ts";

type Action = "hike" | "hold" | "cut";
type Reason = "inflation_fight" | "growth_overheat" | "recession_financial_stress" | "insurance_easing" | "normalization_hike" | "normalization_cut" | "uncertain";
type Transition = "confirmed" | "not_confirmed" | "uncertain";

type Analysis = {
  decision: { action: Action; target_range_lower: number | null; target_range_upper: number | null; change_bps: number | null };
  analysis: { primary_reason: Reason; reason_confidence: number; transition_assessment: Transition; financial_stress_mentioned: boolean; growth_downside_mentioned: boolean; inflation_pressure_mentioned: boolean; summary: string };
  briefing: {
    statement_briefing: string; economy: string | null; inflation: string | null;
    employment: string | null; other: string | null; key_rate_reason: string;
    changes_from_previous: Array<{ title: string; change_type: "added" | "removed" | "strengthened" | "softened" | "reframed" | "maintained"; significance: "high" | "medium" | "low"; previous_expression: string | null; current_expression: string | null; explanation: string }>;
    ai_overall_analysis: string;
  };
};

type Source = { meetingDate: string; sourceUrl: string; isEmergency: boolean };
type EventRow = {
  central_bank: string; meeting_date: string; action: Action | null; primary_reason: Reason | null;
  analysis_status: string; policy_segment: number | null; segment_sequence: number | null;
  target_range_lower?: number | null; target_range_upper?: number | null;
  is_emergency?: boolean; change_bps?: number | null;
  source_url?: string | null;
};

const FED_BASE = "https://www.federalreserve.gov";
const POLICY_PROMPT_VERSION = "v2.0";
const REASON_CONFIDENCE_THRESHOLD = 0.55;
const UNCERTAIN_CONFIDENCE_MAX = 0.549;

const RESPONSE_SCHEMA = {
  type: "object", additionalProperties: false,
  properties: {
    decision: { type: "object", additionalProperties: false, properties: {
      action: { type: "string", enum: ["hike", "hold", "cut"] },
      target_range_lower: { anyOf: [{ type: "number" }, { type: "null" }] },
      target_range_upper: { anyOf: [{ type: "number" }, { type: "null" }] },
      change_bps: { anyOf: [{ type: "integer" }, { type: "null" }] },
    }, required: ["action", "target_range_lower", "target_range_upper", "change_bps"] },
    analysis: { type: "object", additionalProperties: false, properties: {
      primary_reason: { type: "string", enum: ["inflation_fight", "growth_overheat", "recession_financial_stress", "insurance_easing", "normalization_hike", "normalization_cut", "uncertain"] },
      reason_confidence: { type: "number", minimum: 0, maximum: 1 },
      transition_assessment: { type: "string", enum: ["confirmed", "not_confirmed", "uncertain"] },
      financial_stress_mentioned: { type: "boolean" }, growth_downside_mentioned: { type: "boolean" },
      inflation_pressure_mentioned: { type: "boolean" }, summary: { type: "string" },
    }, required: ["primary_reason", "reason_confidence", "transition_assessment", "financial_stress_mentioned", "growth_downside_mentioned", "inflation_pressure_mentioned", "summary"] },
    briefing: { type: "object", additionalProperties: false, properties: {
      statement_briefing: { type: "string" },
      economy: { anyOf: [{ type: "string" }, { type: "null" }] },
      inflation: { anyOf: [{ type: "string" }, { type: "null" }] },
      employment: { anyOf: [{ type: "string" }, { type: "null" }] },
      other: { anyOf: [{ type: "string" }, { type: "null" }] },
      key_rate_reason: { type: "string" },
      changes_from_previous: { type: "array", maxItems: 6, items: { type: "object", additionalProperties: false, properties: {
        title: { type: "string" }, change_type: { type: "string", enum: ["added", "removed", "strengthened", "softened", "reframed", "maintained"] },
        significance: { type: "string", enum: ["high", "medium", "low"] },
        previous_expression: { anyOf: [{ type: "string" }, { type: "null" }] }, current_expression: { anyOf: [{ type: "string" }, { type: "null" }] }, explanation: { type: "string" },
      }, required: ["title", "change_type", "significance", "previous_expression", "current_expression", "explanation"] } },
      ai_overall_analysis: { type: "string" },
    }, required: ["statement_briefing", "economy", "inflation", "employment", "other", "key_rate_reason", "changes_from_previous", "ai_overall_analysis"] },
  }, required: ["decision", "analysis", "briefing"],
};

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } }); }
function errorMessage(error: unknown) { return error instanceof Error ? error.message : typeof error === "object" ? JSON.stringify(error) : String(error); }
function normalizeText(html: string) { return html.replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " ").replace(/&nbsp;|&#160;/gi, " ").replace(/&amp;/gi, "&").replace(/\s+/g, " ").trim(); }
function dateFromUrl(url: string) { const matched = url.match(/(20\d{6})/); return matched ? `${matched[1].slice(0, 4)}-${matched[1].slice(4, 6)}-${matched[1].slice(6, 8)}` : null; }
async function sha256(value: string) { const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)); return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join(""); }

function extractStatementLinks(html: string, assumeScheduled = false) {
  const output: Source[] = [];
  const anchor = /<a\b[^>]*href=["']([^"']+)["'][^>]*>\s*Statement\s*<\/a>/gi;
  for (const match of html.matchAll(anchor)) {
    const sourceUrl = new URL(match[1], FED_BASE).toString();
    const meetingDate = dateFromUrl(sourceUrl);
    const preceding = html.slice(Math.max(0, (match.index || 0) - 1_500), match.index || 0);
    const headings = [...preceding.matchAll(/<h[1-6][^>]*>([\s\S]*?)<\/h[1-6]>/gi)];
    const heading = headings.length ? normalizeText(headings[headings.length - 1][1]) : "";
    if (meetingDate) output.push({ meetingDate, sourceUrl, isEmergency: !(assumeScheduled || /\bMeeting\b/i.test(heading)) });
  }
  // The current FOMC calendar labels the actual statement link as "HTML"
  // below a Statement heading, so collect its stable official URL pattern too.
  const calendarStatement = /href=["']([^"']*\/newsevents\/pressreleases\/monetary20\d{6}a\.htm)["']/gi;
  for (const match of html.matchAll(calendarStatement)) {
    const sourceUrl = new URL(match[1], FED_BASE).toString();
    const meetingDate = dateFromUrl(sourceUrl);
    if (meetingDate) output.push({ meetingDate, sourceUrl, isEmergency: false });
  }
  return output;
}

async function fedSources(mode: "latest" | "backfill") {
  const thisYear = new Date().getUTCFullYear();
  const pages = mode === "backfill"
    ? Array.from({ length: Math.max(0, thisYear - 2000) }, (_, index) => `${FED_BASE}/monetarypolicy/fomchistorical${2000 + index}.htm`).concat(`${FED_BASE}/monetarypolicy/fomccalendars.htm`)
    : [`${FED_BASE}/monetarypolicy/fomccalendars.htm`];
  const discovered: Source[] = [];
  for (const page of pages) {
    const response = await fetch(page, { signal: AbortSignal.timeout(30_000) });
    // Recent years are kept in the current calendar before the Fed publishes
    // a separate historical-by-year page.  Those expected 404s are harmless.
    if (!response.ok && page.includes("fomchistorical")) continue;
    if (!response.ok) throw new Error(`Fed FOMC 목록을 읽지 못했습니다 (${response.status}).`);
    discovered.push(...extractStatementLinks(await response.text(), page.includes("fomccalendars")));
  }
  return [...new Map(discovered.map((item) => [`${item.meetingDate}|${item.sourceUrl}`, item])).values()]
    .filter((item) => item.meetingDate >= "2000-01-01" && item.meetingDate <= new Date().toISOString().slice(0, 10))
    .sort((a, b) => a.meetingDate.localeCompare(b.meetingDate));
}

async function getStatement(sourceUrl: string) {
  const response = await fetch(sourceUrl, { signal: AbortSignal.timeout(30_000) });
  if (!response.ok) throw new Error(`Fed 성명서를 읽지 못했습니다 (${response.status}).`);
  const statement = normalizeText(await response.text());
  if (statement.length < 120) throw new Error("Fed 성명서 본문이 너무 짧습니다.");
  return statement;
}

function implementationNoteUrl(statementUrl: string) {
  return /a\.htm$/i.test(statementUrl) ? statementUrl.replace(/a\.htm$/i, "a1.htm") : null;
}

function transcriptUrl(meetingDate: string) {
  return `${FED_BASE}/mediacenter/files/FOMCpresconf${meetingDate.replaceAll("-", "")}.pdf`;
}

async function optionalOfficialText(url: string) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
    if (!response.ok) return null;
    const text = normalizeText(await response.text());
    return text.length >= 120 ? text : null;
  } catch { return null; }
}

async function officialPdfAvailable(url: string) {
  try {
    const response = await fetch(url, { method: "HEAD", signal: AbortSignal.timeout(30_000) });
    return response.ok && (response.headers.get("content-type") || "").toLowerCase().includes("pdf");
  } catch { return false; }
}

function systemPrompt() {
  const prompt = Deno.env.get("FOMC_POLICY_SYSTEM_PROMPT");
  if (!prompt) throw new Error("FOMC_POLICY_SYSTEM_PROMPT가 설정되지 않았습니다.");
  if (!prompt.trimStart().startsWith(`FOMC 분석 프롬프트 ${POLICY_PROMPT_VERSION}`)) {
    throw new Error(`FOMC_POLICY_SYSTEM_PROMPT를 ${POLICY_PROMPT_VERSION} 원문으로 갱신해야 합니다.`);
  }
  return prompt.replace(/\{\{(?:meeting_metadata|previous_policy_context|fomc_statement|previous_fomc_statement|implementation_note|press_conference_transcript|liquidity_context)\}\}/g, "").trim();
}

async function analyzeStatement(statement: string, meetingDate: string, previous: EventRow | null, previousStatement: string | null, implementationNote: string | null, pressConferenceUrl: string | null, liquidityContext: unknown): Promise<Analysis> {
  const apiKey = Deno.env.get("OPENAI_API_KEY");
  if (!apiKey) throw new Error("OPENAI_API_KEY가 설정되지 않았습니다.");
  const input = {
    meeting_metadata: { central_bank: "fed", meeting_date: meetingDate, source: "Federal Reserve official FOMC statement" },
    previous_policy_context: previous ? { meeting_date: previous.meeting_date, action: previous.action, target_range_lower: previous.target_range_lower, target_range_upper: previous.target_range_upper, primary_reason: previous.primary_reason, policy_segment: previous.policy_segment, segment_sequence: previous.segment_sequence } : null,
    fomc_statement: statement,
    previous_fomc_statement: previousStatement,
    implementation_note: implementationNote,
    press_conference_transcript: pressConferenceUrl ? "아래 첨부된 연준 공식 기자회견 녹취록 PDF" : null,
    liquidity_context: liquidityContext,
  };
  const userContent: Array<Record<string, unknown>> = [{ type: "input_text", text: JSON.stringify(input) }];
  if (pressConferenceUrl) userContent.push({ type: "input_file", file_url: pressConferenceUrl });
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST", headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: Deno.env.get("AI_MODEL_STANDARD") || "gpt-5.6-luna", reasoning: { effort: "low" }, max_output_tokens: 6_000, prompt_cache_key: "macrowatch-fomc-policy-v2.0", input: [{ role: "system", content: [{ type: "input_text", text: systemPrompt() }] }, { role: "user", content: userContent }], text: { format: { type: "json_schema", name: "fomc_policy_analysis", strict: true, schema: RESPONSE_SCHEMA } } }),
  });
  if (!response.ok) throw new Error(`OpenAI FOMC 분석 오류 (${response.status}): ${await response.text()}`);
  const payload = await response.json();
  const output = typeof payload.output_text === "string" ? payload.output_text : payload.output?.flatMap((entry: { content?: Array<{ type?: string; text?: string }> }) => entry.content || []).find((entry: { type?: string }) => entry.type === "output_text")?.text;
  if (typeof output !== "string") throw new Error("OpenAI FOMC 응답에 output_text가 없습니다.");
  return JSON.parse(output) as Analysis;
}

// 금리 변동폭은 가능한 경우 현재·직전 목표금리 범위의 동일한 이동폭으로 계산한다.
// 범위 체계 전환처럼 상·하단 이동폭이 다르면 직접 비교할 수 없으므로, 성명문을
// 바탕으로 AI가 추출한 값만 방향을 정규화해 사용한다. 동결은 항상 0bp다.
function normalizedChangeBps(current: Analysis["decision"], previous: EventRow | null) {
  if (current.action === "hold") return 0;
  const currentLower = current.target_range_lower;
  const currentUpper = current.target_range_upper;
  const previousLower = previous?.target_range_lower;
  const previousUpper = previous?.target_range_upper;
  const isFiniteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
  if (isFiniteNumber(currentLower) && isFiniteNumber(currentUpper) && isFiniteNumber(previousLower) && isFiniteNumber(previousUpper)) {
    const lowerChange = (currentLower - previousLower) * 100;
    const upperChange = (currentUpper - previousUpper) * 100;
    if (Math.abs(lowerChange - upperChange) < 0.01) return Math.round((lowerChange + upperChange) / 2);
  }
  if (Number.isInteger(current.change_bps) && current.change_bps !== 0) {
    const magnitude = Math.abs(current.change_bps!);
    return current.action === "hike" ? magnitude : -magnitude;
  }
  return null;
}

// 프롬프트 규칙을 저장 직전에도 보장해 낮은 신뢰도의 확정 분류와
// uncertain인데 높은 신뢰도를 갖는 모순이 DB에 들어가지 않게 한다.
function normalizeAnalysis(analysis: Analysis, previous: EventRow | null): Analysis {
  let primaryReason = analysis.analysis.primary_reason;
  let reasonConfidence = analysis.analysis.reason_confidence;
  if ((primaryReason === "normalization_hike" && analysis.decision.action !== "hike")
    || (primaryReason === "normalization_cut" && analysis.decision.action !== "cut")) {
    primaryReason = "uncertain";
    reasonConfidence = Math.min(reasonConfidence, UNCERTAIN_CONFIDENCE_MAX);
  }
  if (reasonConfidence < REASON_CONFIDENCE_THRESHOLD) primaryReason = "uncertain";
  if (primaryReason === "uncertain") reasonConfidence = Math.min(reasonConfidence, UNCERTAIN_CONFIDENCE_MAX);
  return {
    decision: { ...analysis.decision, change_bps: normalizedChangeBps(analysis.decision, previous) },
    analysis: {
      ...analysis.analysis,
      primary_reason: primaryReason,
      reason_confidence: reasonConfidence,
      transition_assessment: previous ? analysis.analysis.transition_assessment : "uncertain",
    },
  };
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const body = await request.json().catch(() => ({})) as { bank?: string; mode?: string; limit?: number; years?: number };
    if (body.bank && body.bank !== "fed") return json({ error: "현재는 Fed만 지원합니다." }, 400);
    const mode = body.mode === "backfill" || body.mode === "reanalyze" || body.mode === "recent" ? body.mode : "latest";
    const limit = Math.min(Math.max(Number(body.limit) || 4, 1), 8);
    const recentYears = Math.min(Math.max(Number(body.years) || 2, 1), 5);
    const url = Deno.env.get("SUPABASE_URL"), serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!url || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");
    const supabase = createClient(url, serviceRole);
    if (body.mode === "score") {
      const scored = await recomputePolicyScores(supabase, "fed");
      return json({
        bank: "fed",
        mode: "score",
        score_profile: POLICY_SCORE_PROFILE,
        scored: scored.length,
        confirmed_peaks: scored.filter((row) => row.is_confirmed_rate_peak).length,
        aged_peak_reaches: scored.filter((row) => row.previous_peak_adjustment === 100).length,
      });
    }
    const sources = await fedSources(mode === "latest" || mode === "recent" ? "latest" : "backfill");
    // Daily runs only revisit the newest meeting. This is enough to discover the next
    // statement and later transcript while preventing a v2 rollout from creating NEW
    // notifications for every historical row at once. Historical briefing backfill is
    // an explicit reanalyze operation.
    const recentCutoff = new Date();
    recentCutoff.setUTCFullYear(recentCutoff.getUTCFullYear() - recentYears);
    const selected = mode === "latest"
      ? sources.slice(-1)
      : mode === "recent"
        ? sources.filter((source) => source.meetingDate >= recentCutoff.toISOString().slice(0, 10))
        : sources;
    const { data: existing, error: existingError } = await supabase.from("central_bank_policy_events").select("meeting_date,statement_hash,analysis_status,source_url,is_emergency,analysis_prompt_version,briefing,briefing_revision,briefing_source_state").eq("central_bank", "fed");
    if (existingError) throw existingError;
    type SavedRow = { meeting_date: string; statement_hash: string; analysis_status: string; source_url: string | null; is_emergency: boolean | null; analysis_prompt_version: string | null; briefing: unknown; briefing_revision: number; briefing_source_state: Record<string, unknown> | null };
    const known = new Map((existing || []).map((row: SavedRow) => [row.meeting_date, row]));
    let processed = 0, skipped = 0, failed = 0;
    for (const source of selected) {
      if (processed >= limit) break;
      const saved = known.get(source.meetingDate);
      // A historical backfill fills gaps; it is not a re-analysis command.
      // Trust completed rows so each small batch does not download and hash
      // the entire archive again. Keep changed source metadata synchronized.
      if (mode === "backfill" && saved?.analysis_status === "completed") {
        if (saved.source_url !== source.sourceUrl || saved.is_emergency !== source.isEmergency) {
          const { error: metadataError } = await supabase.from("central_bank_policy_events").update({ source_url: source.sourceUrl, is_emergency: source.isEmergency, updated_at: new Date().toISOString() }).eq("central_bank", "fed").eq("meeting_date", source.meetingDate);
          if (metadataError) throw metadataError;
        }
        skipped += 1;
        continue;
      }
      const statement = await getStatement(source.sourceUrl);
      const statementHash = await sha256(statement);
      const noteUrl = implementationNoteUrl(source.sourceUrl);
      const implementationNote = noteUrl ? await optionalOfficialText(noteUrl) : null;
      const candidateTranscriptUrl = transcriptUrl(source.meetingDate);
      const pressConferenceUrl = await officialPdfAvailable(candidateTranscriptUrl) ? candidateTranscriptUrl : null;
      const priorSourceState = saved?.briefing_source_state || {};
      const sourceState = {
        statement_hash: statementHash,
        implementation_note_url: implementationNote ? noteUrl : null,
        press_conference_url: pressConferenceUrl,
        // Meeting-time liquidity data is immutable once supplied by the collector.
        liquidity_context: priorSourceState.liquidity_context ?? null,
      };
      const sourceStateHash = await sha256(JSON.stringify(sourceState));
      const priorSourceStateHash = typeof priorSourceState.source_state_hash === "string" ? priorSourceState.source_state_hash : null;
      const briefingNeedsRefresh = !saved?.briefing || saved.analysis_prompt_version !== POLICY_PROMPT_VERSION || sourceStateHash !== priorSourceStateHash;
      if (mode !== "reanalyze" && saved?.statement_hash === statementHash && saved.analysis_status === "completed" && !briefingNeedsRefresh) {
        const { error: metadataError } = await supabase.from("central_bank_policy_events").update({ source_url: source.sourceUrl, is_emergency: source.isEmergency, updated_at: new Date().toISOString() }).eq("central_bank", "fed").eq("meeting_date", source.meetingDate);
        if (metadataError) throw metadataError;
        skipped += 1; continue;
      }
      if (mode === "reanalyze" && saved?.analysis_status === "completed" && !briefingNeedsRefresh) {
        skipped += 1; continue;
      }
      processed += 1;
      try {
        if (mode === "reanalyze" && saved?.analysis_status === "completed") {
          const { data: currentRow, error: currentError } = await supabase.from("central_bank_policy_events")
            .select("*").eq("central_bank", "fed").eq("meeting_date", source.meetingDate).single();
          if (currentError) throw currentError;
          const { error: historyError } = await supabase.from("central_bank_policy_analysis_history").upsert({
            central_bank: "fed",
            meeting_date: source.meetingDate,
            analysis_prompt_version: currentRow.analysis_prompt_version,
            reanalyzed_to_version: POLICY_PROMPT_VERSION,
            event_snapshot: currentRow,
          }, { onConflict: "central_bank,meeting_date,reanalyzed_to_version", ignoreDuplicates: true });
          if (historyError) throw historyError;
        }
        const { data: previousRows, error: previousError } = await supabase.from("central_bank_policy_events").select("central_bank,meeting_date,action,target_range_lower,target_range_upper,primary_reason,analysis_status,policy_segment,segment_sequence,source_url").eq("central_bank", "fed").eq("analysis_status", "completed").lt("meeting_date", source.meetingDate).order("meeting_date", { ascending: false }).limit(1);
        if (previousError) throw previousError;
        const previous = previousRows?.[0] as EventRow || null;
        const previousStatement = previousRows?.[0]?.source_url ? await getStatement(previousRows[0].source_url) : null;
        const analysis = normalizeAnalysis(await analyzeStatement(statement, source.meetingDate, previous, previousStatement, implementationNote, pressConferenceUrl, sourceState.liquidity_context), previous);
        const now = new Date().toISOString();
        const isNewBriefing = !saved?.briefing;
        const briefingRevision = isNewBriefing ? 0 : Number(saved?.briefing_revision || 0) + 1;
        const row = { central_bank: "fed", meeting_date: source.meetingDate, source_url: source.sourceUrl, statement_hash: statementHash, is_emergency: source.isEmergency, analysis_status: "completed", analysis_prompt_version: POLICY_PROMPT_VERSION, action: analysis.decision.action, target_range_lower: analysis.decision.target_range_lower, target_range_upper: analysis.decision.target_range_upper, change_bps: analysis.decision.change_bps, ai_primary_reason: analysis.analysis.primary_reason, primary_reason: analysis.analysis.primary_reason, reason_confidence: analysis.analysis.reason_confidence, transition_assessment: analysis.analysis.transition_assessment, financial_stress_mentioned: analysis.analysis.financial_stress_mentioned, growth_downside_mentioned: analysis.analysis.growth_downside_mentioned, inflation_pressure_mentioned: analysis.analysis.inflation_pressure_mentioned, reason_summary: analysis.analysis.summary, briefing: analysis.briefing, briefing_revision: briefingRevision, briefing_source_state: { ...sourceState, source_state_hash: sourceStateHash }, briefing_published_at: isNewBriefing ? now : undefined, briefing_updated_at: now, analyzed_at: now, last_error: null, updated_at: now };
        const { error: saveError } = await supabase.from("central_bank_policy_events").upsert(row, { onConflict: "central_bank,meeting_date" });
        if (saveError) throw saveError;
        // Historical backfills do not notify; only the live latest-meeting flow does.
        if (mode === "latest") {
          const { error: alertError } = await supabase.from("policy_briefing_alerts").upsert({ central_bank: "fed", meeting_date: source.meetingDate, revision: briefingRevision, alert_kind: isNewBriefing ? "new" : "update", status: "pending", updated_at: now }, { onConflict: "central_bank,meeting_date,revision", ignoreDuplicates: true });
          if (alertError) throw alertError;
        }
      } catch (error) {
        failed += 1;
        const { error: saveError } = await supabase.from("central_bank_policy_events").upsert({ central_bank: "fed", meeting_date: source.meetingDate, source_url: source.sourceUrl, statement_hash: statementHash, is_emergency: source.isEmergency, analysis_status: "failed", last_error: errorMessage(error).slice(0, 900), updated_at: new Date().toISOString() }, { onConflict: "central_bank,meeting_date" });
        if (saveError) throw saveError;
      }
    }
    const { count: scoredCount, error: scoredCountError } = await supabase.from("central_bank_policy_events")
      .select("meeting_date", { count: "exact", head: true }).eq("central_bank", "fed").not("policy_index", "is", null);
    if (scoredCountError) throw scoredCountError;
    if ((scoredCount || 0) > 0) await recomputePolicyScores(supabase, "fed");
    const hasMore = selected.some((source) => {
      const saved = known.get(source.meetingDate);
      return !saved || saved.analysis_status !== "completed"
        || (mode === "reanalyze" && saved.analysis_prompt_version !== POLICY_PROMPT_VERSION)
        || (mode === "recent" && (!saved.briefing || saved.analysis_prompt_version !== POLICY_PROMPT_VERSION));
    }) && processed >= limit;
    return json({ bank: "fed", mode, discovered: selected.length, processed, skipped, failed, has_more: hasMore });
  } catch (error) { return json({ error: errorMessage(error) }, 500); }
});
