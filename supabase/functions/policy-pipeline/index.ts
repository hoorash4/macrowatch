import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type Action = "hike" | "hold" | "cut";
type Reason = "inflation_fight" | "growth_overheat" | "recession_financial_stress" | "insurance_easing" | "uncertain";
type Transition = "confirmed" | "not_confirmed" | "uncertain";

type Analysis = {
  decision: { action: Action; target_range_lower: number | null; target_range_upper: number | null; change_bps: number | null };
  analysis: { primary_reason: Reason; reason_confidence: number; transition_assessment: Transition; financial_stress_mentioned: boolean; growth_downside_mentioned: boolean; inflation_pressure_mentioned: boolean; summary: string };
};

type Source = { meetingDate: string; sourceUrl: string };
type EventRow = {
  central_bank: string; meeting_date: string; action: Action | null; primary_reason: Reason | null;
  analysis_status: string; policy_segment: number | null; segment_sequence: number | null;
};

const FED_BASE = "https://www.federalreserve.gov";
const SCORE_PROFILE_VERSION = "fed-policy-v1";

// Positive values raise the later MSI stress contribution.  The event impulse
// is attenuated by the number of consecutive decisions in the same regime.
const STRESS_BASE: Record<Reason, Record<Action, number>> = {
  inflation_fight: { hike: 100, hold: 0, cut: 0 },
  growth_overheat: { hike: -50, hold: 0, cut: 0 },
  recession_financial_stress: { hike: 0, hold: 0, cut: 100 },
  insurance_easing: { hike: 0, hold: 0, cut: -70 },
  uncertain: { hike: 0, hold: 0, cut: 0 },
};

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
      primary_reason: { type: "string", enum: ["inflation_fight", "growth_overheat", "recession_financial_stress", "insurance_easing", "uncertain"] },
      reason_confidence: { type: "number", minimum: 0, maximum: 1 },
      transition_assessment: { type: "string", enum: ["confirmed", "not_confirmed", "uncertain"] },
      financial_stress_mentioned: { type: "boolean" }, growth_downside_mentioned: { type: "boolean" },
      inflation_pressure_mentioned: { type: "boolean" }, summary: { type: "string" },
    }, required: ["primary_reason", "reason_confidence", "transition_assessment", "financial_stress_mentioned", "growth_downside_mentioned", "inflation_pressure_mentioned", "summary"] },
  }, required: ["decision", "analysis"],
};

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } }); }
function errorMessage(error: unknown) { return error instanceof Error ? error.message : typeof error === "object" ? JSON.stringify(error) : String(error); }
function normalizeText(html: string) { return html.replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " ").replace(/&nbsp;|&#160;/gi, " ").replace(/&amp;/gi, "&").replace(/\s+/g, " ").trim(); }
function dateFromUrl(url: string) { const matched = url.match(/(20\d{6})/); return matched ? `${matched[1].slice(0, 4)}-${matched[1].slice(4, 6)}-${matched[1].slice(6, 8)}` : null; }
async function sha256(value: string) { const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)); return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join(""); }

function extractStatementLinks(html: string) {
  const output: Source[] = [];
  const anchor = /<a\b[^>]*href=["']([^"']+)["'][^>]*>\s*Statement\s*<\/a>/gi;
  for (const match of html.matchAll(anchor)) {
    const sourceUrl = new URL(match[1], FED_BASE).toString();
    const meetingDate = dateFromUrl(sourceUrl);
    if (meetingDate) output.push({ meetingDate, sourceUrl });
  }
  // The current FOMC calendar labels the actual statement link as "HTML"
  // below a Statement heading, so collect its stable official URL pattern too.
  const calendarStatement = /href=["']([^"']*\/newsevents\/pressreleases\/monetary20\d{6}a\.htm)["']/gi;
  for (const match of html.matchAll(calendarStatement)) {
    const sourceUrl = new URL(match[1], FED_BASE).toString();
    const meetingDate = dateFromUrl(sourceUrl);
    if (meetingDate) output.push({ meetingDate, sourceUrl });
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
    discovered.push(...extractStatementLinks(await response.text()));
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

function systemPrompt() {
  const prompt = Deno.env.get("FOMC_POLICY_SYSTEM_PROMPT");
  if (!prompt) throw new Error("FOMC_POLICY_SYSTEM_PROMPT가 설정되지 않았습니다.");
  return prompt.replace(/\{\{meeting_metadata\}\}|\{\{previous_policy_context\}\}|\{\{fomc_statement\}\}/g, "").trim();
}

async function analyzeStatement(statement: string, meetingDate: string, previous: EventRow | null): Promise<Analysis> {
  const apiKey = Deno.env.get("OPENAI_API_KEY");
  if (!apiKey) throw new Error("OPENAI_API_KEY가 설정되지 않았습니다.");
  const input = {
    meeting_metadata: { central_bank: "fed", meeting_date: meetingDate, source: "Federal Reserve official FOMC statement" },
    previous_policy_context: previous ? { meeting_date: previous.meeting_date, action: previous.action, primary_reason: previous.primary_reason, policy_segment: previous.policy_segment, segment_sequence: previous.segment_sequence } : null,
    fomc_statement: statement,
  };
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST", headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: Deno.env.get("AI_MODEL_STANDARD") || "gpt-5.6-luna", reasoning: { effort: "low" }, max_output_tokens: 1_200, prompt_cache_key: "macrowatch-fomc-policy-v1", input: [{ role: "system", content: [{ type: "input_text", text: systemPrompt() }] }, { role: "user", content: [{ type: "input_text", text: JSON.stringify(input) }] }], text: { format: { type: "json_schema", name: "fomc_policy_analysis", strict: true, schema: RESPONSE_SCHEMA } } }),
  });
  if (!response.ok) throw new Error(`OpenAI FOMC 분석 오류 (${response.status}): ${await response.text()}`);
  const payload = await response.json();
  const output = typeof payload.output_text === "string" ? payload.output_text : payload.output?.flatMap((entry: { content?: Array<{ type?: string; text?: string }> }) => entry.content || []).find((entry: { type?: string }) => entry.type === "output_text")?.text;
  if (typeof output !== "string") throw new Error("OpenAI FOMC 응답에 output_text가 없습니다.");
  return JSON.parse(output) as Analysis;
}

function regimeKey(row: Pick<EventRow, "action" | "primary_reason">) { return row.action && row.primary_reason ? `${row.action}:${row.primary_reason}` : null; }

async function recomputeSegments(supabase: ReturnType<typeof createClient>) {
  const { data, error } = await supabase.from("central_bank_policy_events").select("central_bank,meeting_date,action,primary_reason,analysis_status,policy_segment,segment_sequence").eq("central_bank", "fed").eq("analysis_status", "completed").order("meeting_date");
  if (error) throw error;
  let segment = 0, sequence = 0, activeKey: string | null = null;
  const updates = (data || []).map((row: EventRow) => {
    const key = regimeKey(row);
    if (!key || row.action === "hold") { activeKey = null; sequence = 0; return { central_bank: "fed", meeting_date: row.meeting_date, policy_segment: null, segment_sequence: 0, policy_impulse: 0, policy_stress_contribution: 0, score_profile_version: SCORE_PROFILE_VERSION, updated_at: new Date().toISOString() }; }
    if (key !== activeKey) { segment += 1; sequence = 1; activeKey = key; } else sequence += 1;
    const base = STRESS_BASE[row.primary_reason!][row.action!];
    return { central_bank: "fed", meeting_date: row.meeting_date, policy_segment: segment, segment_sequence: sequence, policy_impulse: Number((base / sequence).toFixed(3)), policy_stress_contribution: base, score_profile_version: SCORE_PROFILE_VERSION, updated_at: new Date().toISOString() };
  });
  for (const update of updates) {
    const { central_bank, meeting_date, ...values } = update;
    const { error: updateError } = await supabase.from("central_bank_policy_events").update(values).eq("central_bank", central_bank).eq("meeting_date", meeting_date);
    if (updateError) throw updateError;
  }
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const body = await request.json().catch(() => ({})) as { bank?: string; mode?: string; limit?: number };
    if (body.bank && body.bank !== "fed") return json({ error: "현재는 Fed만 지원합니다." }, 400);
    const mode = body.mode === "backfill" ? "backfill" : "latest";
    const limit = Math.min(Math.max(Number(body.limit) || 4, 1), 8);
    const url = Deno.env.get("SUPABASE_URL"), serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!url || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");
    const supabase = createClient(url, serviceRole);
    const sources = await fedSources(mode);
    const selected = mode === "backfill" ? sources : sources.filter((source) => source.meetingDate >= `${new Date().getUTCFullYear() - 1}-01-01`);
    const { data: existing, error: existingError } = await supabase.from("central_bank_policy_events").select("meeting_date,statement_hash,analysis_status").eq("central_bank", "fed");
    if (existingError) throw existingError;
    const known = new Map((existing || []).map((row: { meeting_date: string; statement_hash: string; analysis_status: string }) => [row.meeting_date, row]));
    let processed = 0, skipped = 0, failed = 0;
    for (const source of selected) {
      if (processed >= limit) break;
      const statement = await getStatement(source.sourceUrl);
      const statementHash = await sha256(statement);
      const saved = known.get(source.meetingDate);
      if (saved?.statement_hash === statementHash && saved.analysis_status === "completed") { skipped += 1; continue; }
      processed += 1;
      try {
        const { data: previousRows, error: previousError } = await supabase.from("central_bank_policy_events").select("central_bank,meeting_date,action,primary_reason,analysis_status,policy_segment,segment_sequence").eq("central_bank", "fed").eq("analysis_status", "completed").lt("meeting_date", source.meetingDate).order("meeting_date", { ascending: false }).limit(1);
        if (previousError) throw previousError;
        const analysis = await analyzeStatement(statement, source.meetingDate, previousRows?.[0] as EventRow || null);
        const row = { central_bank: "fed", meeting_date: source.meetingDate, source_url: source.sourceUrl, statement_hash: statementHash, analysis_status: "completed", action: analysis.decision.action, target_range_lower: analysis.decision.target_range_lower, target_range_upper: analysis.decision.target_range_upper, change_bps: analysis.decision.change_bps, primary_reason: analysis.analysis.primary_reason, reason_confidence: analysis.analysis.reason_confidence, transition_assessment: analysis.analysis.transition_assessment, financial_stress_mentioned: analysis.analysis.financial_stress_mentioned, growth_downside_mentioned: analysis.analysis.growth_downside_mentioned, inflation_pressure_mentioned: analysis.analysis.inflation_pressure_mentioned, reason_summary: analysis.analysis.summary, analyzed_at: new Date().toISOString(), last_error: null, updated_at: new Date().toISOString() };
        const { error: saveError } = await supabase.from("central_bank_policy_events").upsert(row, { onConflict: "central_bank,meeting_date" });
        if (saveError) throw saveError;
      } catch (error) {
        failed += 1;
        const { error: saveError } = await supabase.from("central_bank_policy_events").upsert({ central_bank: "fed", meeting_date: source.meetingDate, source_url: source.sourceUrl, statement_hash: statementHash, analysis_status: "failed", last_error: errorMessage(error).slice(0, 900), updated_at: new Date().toISOString() }, { onConflict: "central_bank,meeting_date" });
        if (saveError) throw saveError;
      }
    }
    await recomputeSegments(supabase);
    const hasMore = selected.some((source) => {
      const saved = known.get(source.meetingDate);
      return !saved || saved.analysis_status !== "completed";
    }) && processed >= limit;
    return json({ bank: "fed", mode, discovered: selected.length, processed, skipped, failed, has_more: hasMore });
  } catch (error) { return json({ error: errorMessage(error) }, 500); }
});
