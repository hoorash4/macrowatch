import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { PIVOT_ANALYSIS_SCHEMA, PIVOT_SCHEMA_VERSION, type PivotAnalysisOutput } from "../_shared/pivot/pivot-schema.ts";

const PROMPT_VERSION = Deno.env.get("PIVOT_ANALYSIS_PROMPT_VERSION") || "pivot-v1";
const MODEL = Deno.env.get("PIVOT_AI_MODEL") || Deno.env.get("AI_MODEL_STANDARD") || "gpt-5.6-luna";
const MAX_SERIES_POINTS = 1200;
const ANOMALY_VALIDATION_VERSION = "web-search-v1";
const PIVOT_REASON_INSTRUCTION = `
피봇 판단 근거 기록 규칙은 반드시 지켜라.
- 모든 pivot에 reason을 반드시 기록한다. reason은 사후 검증용이며 1~2문장으로 간단명료하게 쓴다.
- A의 reason에는 부모 추세가 무엇이었는지, 어떤 상위 구조가 깨졌는지, 어떤 후속 구조가 새로운 추세의 지속성을 확인했는지를 명시한다.
- B의 reason에는 횡보 전후의 부모 추세 방향이 실제로 어떻게 달라졌는지를 명시한다.
- C/D도 짧게 판단 근거를 남기되, 특히 A/B가 아닌 이유가 드러나게 쓴다.
- 단순히 '상승 후 하락', '저점 후 상승', '큰 폭으로 움직임'처럼 결과나 진폭만 반복하지 마라.
- 부모 추세의 흐름을 바꾸지 못한 반대 방향 움직임은 자식 추세일 뿐이며 A의 근거가 될 수 없다.
`;
const POST_TREND_INSTRUCTION = `
A/B 피봇의 post_trend 출력 규칙은 반드시 지켜라.
- A 또는 B에는 post_trend를 반드시 객체로 출력한다.
- post_trend.direction은 up/down/sideways 중 하나다.
- post_trend.end_date는 해당 피봇을 시작점으로 한 가장 상위의 단일 부모 추세가 구조적으로 끝난 날짜다.
- 작은 조정, 반등, 짧은 횡보나 국소 파동으로 부모 추세를 끊지 마라.
- C 또는 D에는 post_trend를 분석하지 말고 반드시 null을 출력한다.
- post_trend 결과를 이용해 이미 결정한 A/B/C/D 등급이나 기존 regime을 다시 바꾸지 마라.
`;
const ANOMALY_WEB_INSTRUCTION = `
특이점(anomaly) 규칙은 다음을 반드시 지켜라.
- 차트에서 급격하거나 이례적으로 보이는 움직임은 anomaly의 후보일 뿐이며, 차트 모양만으로 anomaly를 확정해서는 안 된다.
- anomaly 후보를 발견하면 그 움직임의 실제 시작점(movement_start_date)을 먼저 정한다.
- 반드시 웹 검색을 사용하여 movement_start_date 이전 3개월 이내의 주요 뉴스와 사건을 조사한다.
- 그 기간 안에 당시 시장 참여자가 절대 예상할 수 없었던 돌발 외생 사건이 실제로 확인되고, 그 사건이 해당 움직임을 설명할 합리적 연결고리가 있을 때만 anomalies 배열에 포함한다.
- 이미 알려져 있던 위험, 누적된 불균형, 진행 중이던 위기의 연장, 예정된 정책/일정, 이미 인식되던 취약성의 현실화는 anomaly 근거가 될 수 없다.
- 조건을 만족하는 사건을 확인하지 못하면 후보가 아무리 급격해 보여도 anomaly로 출력하지 말고 버린다.
- anomaly의 date는 움직임 시작일이 아니라 그 충격 파동이 만든 실제 극값 날짜다. 급등은 최고점, 급락은 최저점이다.
- event_name, event_date, reason, source_urls에는 실제 웹 검색으로 확인한 근거만 기록한다. 사건명이나 출처를 추측하거나 만들어내지 마라.
- search_window_start는 movement_start_date의 3개월 전, search_window_end는 movement_start_date로 기록한다.
- anomalies 배열에 들어가는 모든 항목은 unexpected_event=true여야 한다. 이 조건을 확신할 수 없으면 anomalies에 넣지 마라.
`;

type Point = { date: string; value: number };
type Payload = {
  case_code: string;
  case_name?: string;
  index_code: string;
  series_code: string;
  series_name?: string;
  cycle: { start_date: string; peak_date?: string | null; trough_date?: string | null };
  display_window: { start_date: string; end_date: string; left_ratio?: number; main_ratio?: number; right_ratio?: number };
  indicator_points: Point[];
  chart_image_data_url: string;
  chart_sha256?: string | null;
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8" } });
}

function outputText(payload: Record<string, unknown>) {
  if (typeof payload.output_text === "string") return payload.output_text;
  const output = Array.isArray(payload.output) ? payload.output : [];
  for (const item of output) {
    if (!item || typeof item !== "object") continue;
    const content = Array.isArray((item as { content?: unknown[] }).content) ? (item as { content: unknown[] }).content : [];
    for (const part of content) {
      if (part && typeof part === "object" && (part as { type?: unknown }).type === "output_text"
        && typeof (part as { text?: unknown }).text === "string") return (part as { text: string }).text;
    }
  }
  return null;
}

function validDate(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T00:00:00Z`));
}

function minusThreeMonths(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  const source = new Date(Date.UTC(year, month - 1, day));
  const targetMonth = source.getUTCMonth() - 3;
  const targetYear = source.getUTCFullYear() + Math.floor(targetMonth / 12);
  const normalizedMonth = ((targetMonth % 12) + 12) % 12;
  const lastDay = new Date(Date.UTC(targetYear, normalizedMonth + 1, 0)).getUTCDate();
  const target = new Date(Date.UTC(targetYear, normalizedMonth, Math.min(day, lastDay)));
  return target.toISOString().slice(0, 10);
}

function validateInput(body: unknown): Payload {
  if (!body || typeof body !== "object") throw new Error("요청 본문이 없습니다.");
  const value = body as Record<string, unknown>;
  const cycle = value.cycle as Record<string, unknown> | undefined;
  const display = value.display_window as Record<string, unknown> | undefined;
  const points = Array.isArray(value.indicator_points) ? value.indicator_points : [];
  if (![value.case_code, value.index_code, value.series_code].every(item => typeof item === "string" && item.trim())) throw new Error("case_code/index_code/series_code가 필요합니다.");
  if (!cycle || !validDate(cycle.start_date) || (cycle.peak_date != null && !validDate(cycle.peak_date)) || (cycle.trough_date != null && !validDate(cycle.trough_date))) throw new Error("cycle 날짜가 올바르지 않습니다.");
  if (!display || !validDate(display.start_date) || !validDate(display.end_date) || String(display.start_date) >= String(display.end_date)) throw new Error("display_window가 올바르지 않습니다.");
  const indicatorPoints = points.map((item) => {
    const row = item as Record<string, unknown>;
    return { date: String(row.date || ""), value: Number(row.value) };
  }).filter(row => validDate(row.date) && Number.isFinite(row.value)).sort((a,b) => a.date.localeCompare(b.date));
  if (indicatorPoints.length < 2) throw new Error("indicator_points가 부족합니다.");
  if (typeof value.chart_image_data_url !== "string" || !value.chart_image_data_url.startsWith("data:image/png;base64,")) throw new Error("PNG chart_image_data_url이 필요합니다.");
  return {
    case_code: String(value.case_code), case_name: typeof value.case_name === "string" ? value.case_name : undefined,
    index_code: String(value.index_code), series_code: String(value.series_code), series_name: typeof value.series_name === "string" ? value.series_name : undefined,
    cycle: { start_date: String(cycle.start_date), peak_date: cycle.peak_date == null ? null : String(cycle.peak_date), trough_date: cycle.trough_date == null ? null : String(cycle.trough_date) },
    display_window: { start_date: String(display.start_date), end_date: String(display.end_date), left_ratio: Number(display.left_ratio ?? .15), main_ratio: Number(display.main_ratio ?? .70), right_ratio: Number(display.right_ratio ?? .15) },
    indicator_points: indicatorPoints,
    chart_image_data_url: value.chart_image_data_url,
    chart_sha256: typeof value.chart_sha256 === "string" ? value.chart_sha256 : null,
  };
}

function compactPoints(points: Point[], limit = MAX_SERIES_POINTS) {
  if (points.length <= limit) return points;
  const buckets = Math.max(1, Math.floor((limit - 2) / 2));
  const step = (points.length - 2) / buckets;
  const selected = new Map<string, Point>([[points[0].date, points[0]], [points.at(-1)!.date, points.at(-1)!]]);
  for (let bucket = 0; bucket < buckets; bucket++) {
    const from = 1 + Math.floor(bucket * step), to = Math.min(points.length - 1, 1 + Math.floor((bucket + 1) * step));
    const slice = points.slice(from, Math.max(from + 1, to));
    if (!slice.length) continue;
    const low = slice.reduce((a,b) => b.value < a.value ? b : a), high = slice.reduce((a,b) => b.value > a.value ? b : a);
    selected.set(low.date, low); selected.set(high.date, high);
  }
  return [...selected.values()].sort((a,b) => a.date.localeCompare(b.date)).slice(0, limit);
}

function nearestPoint(points: Point[], date: string) {
  const target = Date.parse(`${date}T00:00:00Z`);
  let best = points[0], distance = Math.abs(Date.parse(`${best.date}T00:00:00Z`) - target);
  for (const point of points.slice(1)) {
    const next = Math.abs(Date.parse(`${point.date}T00:00:00Z`) - target);
    if (next < distance) { best = point; distance = next; }
  }
  return best;
}

function normalizeAnalysis(raw: PivotAnalysisOutput, points: Point[], from: string, to: string): PivotAnalysisOutput {
  const inRange = (date: string) => date >= from && date <= to;
  const pivots = (raw.pivots || []).filter(item => validDate(item.date) && inRange(item.date)).map(item => {
    const point = nearestPoint(points, item.date);
    const grade = String(item.grade || "").toUpperCase();
    let postTrend = item.post_trend ?? null;
    if (grade === "A" || grade === "B") {
      if (!postTrend || typeof postTrend !== "object") throw new Error(`A/B post_trend가 없습니다: ${point.date}`);
      const direction = String(postTrend.direction || "");
      const endDate = String(postTrend.end_date || "");
      if (!["up", "down", "sideways"].includes(direction) || !validDate(endDate) || endDate < point.date || endDate > to) {
        throw new Error(`A/B post_trend가 올바르지 않습니다: ${point.date}`);
      }
      postTrend = { direction: direction as "up" | "down" | "sideways", end_date: endDate };
    } else {
      postTrend = null;
    }
    return { ...item, post_trend: postTrend, reason: String(item.reason || "").trim().slice(0, 400), date: point.date, value: point.value, confidence: Math.max(0, Math.min(1, Number(item.confidence))) };
  });
  const anomalies = (raw.anomalies || []).filter(item => {
    if (!validDate(item.movement_start_date) || !validDate(item.date) || !validDate(item.event_date)) return false;
    if (!inRange(item.movement_start_date) || !inRange(item.date)) return false;
    if (item.unexpected_event !== true) return false;
    const expectedStart = minusThreeMonths(item.movement_start_date);
    if (item.event_date < expectedStart || item.event_date > item.movement_start_date) return false;
    if (!Array.isArray(item.source_urls) || item.source_urls.length < 1) return false;
    if (!String(item.event_name || "").trim() || !String(item.reason || "").trim()) return false;
    return true;
  }).map(item => {
    const point = nearestPoint(points, item.date);
    const movementStart = nearestPoint(points, item.movement_start_date).date;
    return {
      ...item,
      movement_start_date: movementStart,
      date: point.date,
      value: point.value,
      search_window_start: minusThreeMonths(movementStart),
      search_window_end: movementStart,
      source_urls: item.source_urls.slice(0, 5),
      confidence: Math.max(0, Math.min(1, Number(item.confidence))),
    };
  });
  const regimes = (raw.regimes || []).filter(item => validDate(item.start_date) && inRange(item.start_date)).map(item => ({
    ...item,
    start_date: nearestPoint(points, item.start_date).date,
    end_date: item.end_date && validDate(item.end_date) ? nearestPoint(points, item.end_date).date : null,
    confidence: Math.max(0, Math.min(1, Number(item.confidence))),
  })).filter(item => !item.end_date || item.end_date >= item.start_date);
  return { regimes, pivots, anomalies };
}

function supabaseClient() {
  const url = Deno.env.get("SUPABASE_URL"), key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) throw new Error("Supabase 서버 설정이 없습니다.");
  return createClient(url, key);
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  try {
    const input = validateInput(await req.json());
    const prompt = Deno.env.get("PIVOT_ANALYSIS_PROMPT"), apiKey = Deno.env.get("OPENAI_API_KEY");
    if (!prompt) throw new Error("PIVOT_ANALYSIS_PROMPT가 설정되지 않았습니다.");
    if (!apiKey) throw new Error("OPENAI_API_KEY가 설정되지 않았습니다.");
    const compact = compactPoints(input.indicator_points);
    const metadata = {
      case_code: input.case_code, case_name: input.case_name || null, index_code: input.index_code,
      series_code: input.series_code, series_name: input.series_name || null,
      cycle: input.cycle, display_window: input.display_window,
      instruction: "상단 시장지수와 하단 지표는 동일한 X축이다. 하단 지표만 피봇/횡보/특이점 분석 대상으로 삼고, 시장 START/PEAK/TROUGH는 시간축 문맥으로만 사용하며 그 위치에 피봇을 강제로 만들지 마라.",
      pivot_reason_instruction: PIVOT_REASON_INSTRUCTION,
      post_trend_instruction: POST_TREND_INSTRUCTION,
      anomaly_instruction: ANOMALY_WEB_INSTRUCTION,
      indicator_points: compact,
    };
    const response = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: MODEL,
        reasoning: { effort: "medium" },
        max_output_tokens: 5_000,
        prompt_cache_key: "macrowatch-pivot-analysis-v5",
        tools: [{ type: "web_search_preview", search_context_size: "medium" }],
        tool_choice: "required",
        include: ["web_search_call.action.sources"],
        input: [
          { role: "system", content: [{ type: "input_text", text: prompt + "\n\n" + PIVOT_REASON_INSTRUCTION + "\n\n" + ANOMALY_WEB_INSTRUCTION }] },
          { role: "user", content: [
            { type: "input_text", text: JSON.stringify(metadata) },
            { type: "input_image", image_url: input.chart_image_data_url, detail: "high" },
          ] },
        ],
        text: { format: { type: "json_schema", name: "pivot_analysis", strict: true, schema: PIVOT_ANALYSIS_SCHEMA } },
      }),
    });
    if (!response.ok) throw new Error(`OpenAI pivot 분석 오류 (${response.status}): ${(await response.text()).slice(0,1000)}`);
    const responsePayload = await response.json() as Record<string, unknown>;
    const text = outputText(responsePayload);
    if (!text) throw new Error("OpenAI 응답에 output_text가 없습니다.");
    const parsed = JSON.parse(text) as PivotAnalysisOutput;
    const analysis = normalizeAnalysis(parsed, input.indicator_points, input.display_window.start_date, input.display_window.end_date);
    const now = new Date().toISOString();
    const row = {
      case_code: input.case_code, index_code: input.index_code, series_code: input.series_code,
      display_start: input.display_window.start_date, display_end: input.display_window.end_date,
      cycle_start: input.cycle.start_date, cycle_peak: input.cycle.peak_date, cycle_trough: input.cycle.trough_date,
      model: MODEL, prompt_version: PROMPT_VERSION, schema_version: PIVOT_SCHEMA_VERSION,
      regimes: analysis.regimes, pivots: analysis.pivots, anomalies: analysis.anomalies,
      anomaly_validation_version: ANOMALY_VALIDATION_VERSION,
      source_point_count: input.indicator_points.length, chart_sha256: input.chart_sha256 || null,
      analyzed_at: now, updated_at: now,
    };
    const supabase = supabaseClient();
    const { error } = await supabase.from("historical_indicator_ai_analysis").upsert(row, { onConflict: "case_code,index_code,series_code" });
    if (error) throw error;
    return json({ ok: true, model: MODEL, prompt_version: PROMPT_VERSION, schema_version: PIVOT_SCHEMA_VERSION, anomaly_validation_version: ANOMALY_VALIDATION_VERSION, analysis });
  } catch (error) {
    console.error(error);
    return json({ error: error instanceof Error ? error.message : String(error) }, 500);
  }
});
