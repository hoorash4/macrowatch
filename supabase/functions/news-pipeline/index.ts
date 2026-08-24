import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { AI_POLICY } from "../_shared/ai-policy.ts";
import { analyzeCandidates } from "../_shared/openai-adapter.ts";
import type { AnalyzedEvent, Candidate, SourceName } from "../_shared/news-types.ts";

const DEFAULT_LOOKBACK_HOURS = 24;
const MAX_LOOKBACK_HOURS = 15 * 24;

const SOURCE_FEEDS: Record<SourceName, string> = {
  yonhap: "https://www.yna.co.kr/rss/news.xml",
  hankyung: "https://www.hankyung.com/feed/economy",
  gdelt: "https://api.gdeltproject.org/api/v2/doc/doc",
};

const CATEGORY_TERMS = [
  "금리", "물가", "인플레이션", "고용", "성장", "경기", "관세", "무역",
  "제재", "원유", "유가", "가스", "원자재", "환율", "채권", "증시",
  "주가", "금융", "은행", "기업", "반도체", "ai", "인공지능", "전쟁",
  "분쟁", "선거", "규제", "공급망", "fed", "fomc", "tariff", "rate",
  "inflation", "employment", "oil", "sanction", "market", "stock",
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function normalizeText(value: string) {
  return value.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

function parseDate(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function parseGdeltDate(value: string | null) {
  if (!value) return null;
  const match = value.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/);
  if (!match) return parseDate(value);
  return parseDate(`${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}Z`);
}

async function hashText(value: string) {
  const bytes = new TextEncoder().encode(value.toLowerCase().replace(/\W+/g, " ").trim());
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function withinLookbackWindow(date: string | null, lookbackHours: number, now = Date.now()) {
  if (!date) return true;
  const timestamp = Date.parse(date);
  return timestamp >= now - lookbackHours * 60 * 60 * 1000 && timestamp <= now + 5 * 60 * 1000;
}

function isMarketRelevant(text: string) {
  const normalized = text.toLowerCase();
  return CATEGORY_TERMS.some((term) => normalized.includes(term));
}

function xmlItems(xml: string) {
  const document = new DOMParser().parseFromString(xml, "application/xml");
  if (!document) throw new Error("RSS XML을 읽지 못했습니다.");
  return [...document.querySelectorAll("item")].map((item) => ({
    text: normalizeText(item.querySelector("title")?.textContent || ""),
    url: item.querySelector("link")?.textContent?.trim() || null,
    publishedAt: parseDate(
      item.querySelector("pubDate")?.textContent || item.querySelector("date")?.textContent || null,
    ),
  }));
}

async function fetchRss(source: Exclude<SourceName, "gdelt">, lookbackHours: number): Promise<Candidate[]> {
  const response = await fetch(SOURCE_FEEDS[source], { headers: { "User-Agent": "MacroWatch/1.0" } });
  if (!response.ok) throw new Error(`${source} RSS 오류 (${response.status})`);
  const items = xmlItems(await response.text());
  const candidates: Candidate[] = [];
  for (const item of items) {
    if (!item.text || !withinLookbackWindow(item.publishedAt, lookbackHours) || !isMarketRelevant(item.text)) continue;
    candidates.push({ source, itemHash: await hashText(item.text), ...item });
  }
  return candidates;
}

async function fetchGdelt(lookbackHours: number): Promise<Candidate[]> {
  const url = new URL(SOURCE_FEEDS.gdelt);
  url.search = new URLSearchParams({
    query: "(economy OR finance OR stocks OR market OR tariff OR inflation OR interest rate)",
    mode: "artlist",
    format: "json",
    maxrecords: "250",
    sort: "datedesc",
    timespan: `${lookbackHours}h`,
  }).toString();
  const response = await fetch(url);
  if (!response.ok) throw new Error(`GDELT 오류 (${response.status})`);
  const data = await response.json();
  const candidates: Candidate[] = [];
  for (const item of data.articles || []) {
    const text = normalizeText(`${item.title || ""} ${item.domain || ""}`);
    const publishedAt = parseGdeltDate(item.seendate || null);
    if (!text || !withinLookbackWindow(publishedAt, lookbackHours) || !isMarketRelevant(text)) continue;
    candidates.push({
      source: "gdelt",
      itemHash: await hashText(text),
      publishedAt,
      text,
      url: item.url || null,
    });
  }
  return candidates;
}

function deduplicate(candidates: Candidate[]) {
  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    if (seen.has(candidate.itemHash)) return false;
    seen.add(candidate.itemHash);
    return true;
  });
}

async function collectCandidates(lookbackHours: number) {
  const results = await Promise.allSettled([
    fetchRss("yonhap", lookbackHours),
    fetchRss("hankyung", lookbackHours),
    fetchGdelt(lookbackHours),
  ]);
  const candidates = results.flatMap((result) => result.status === "fulfilled" ? result.value : []);
  const errors = results.flatMap((result, index) => result.status === "rejected" ? [{ source: ["yonhap", "hankyung", "gdelt"][index], error: String(result.reason) }] : []);
  return { candidates: deduplicate(candidates), errors };
}

async function persistAnalysis(events: AnalyzedEvent[], candidates: Candidate[]) {
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) throw new Error("Supabase 서버 설정이 없습니다.");
  const supabase = createClient(supabaseUrl, serviceRoleKey);
  const candidatesByHash = new Map(candidates.map((candidate) => [candidate.itemHash, candidate]));
  const dates = new Set<string>();

  for (const event of events) {
    const sourceItemHashes = [...new Set(event.sourceItemHashes)].filter((hash) => candidatesByHash.has(hash));
    if (!sourceItemHashes.length) continue;
    const eventKey = `${event.eventDate}:${sourceItemHashes.slice().sort().join(":")}`;
    const sourceCount = new Set(sourceItemHashes.map((hash) => candidatesByHash.get(hash)?.source)).size;
    const { data, error } = await supabase.from("news_events").upsert({
      event_key: eventKey,
      event_date: event.eventDate,
      event_at: event.eventAt,
      summary: event.summary,
      category: event.category,
      impact_scope: event.impactScope,
      transmission_channels: event.transmissionChannels,
      market_relevance: event.marketRelevance,
      short_term_impact: event.shortTermImpact,
      five_day_impact: event.fiveDayImpact,
      confidence: event.confidence,
      source_count: sourceCount,
      analysis_version: AI_POLICY.promptVersion,
      updated_at: new Date().toISOString(),
    }, { onConflict: "event_key" }).select("id").single();
    if (error) throw error;

    const sourceRows = sourceItemHashes.map((hash) => {
      const candidate = candidatesByHash.get(hash)!;
      return {
        event_id: data.id,
        source_name: candidate.source,
        source_item_hash: candidate.itemHash,
        published_at: candidate.publishedAt,
      };
    });
    const { error: sourceError } = await supabase.from("news_event_sources").upsert(sourceRows, {
      onConflict: "source_name,source_item_hash",
    });
    if (sourceError) throw sourceError;
    dates.add(event.eventDate);
  }

  for (const eventDate of dates) await refreshDailySentiment(supabase, eventDate);
  return { events: events.length, dates: [...dates].sort() };
}

async function refreshDailySentiment(supabase: ReturnType<typeof createClient>, eventDate: string) {
  const { data, error } = await supabase.from("news_events")
    .select("short_term_impact, market_relevance")
    .eq("event_date", eventDate);
  if (error) throw error;
  const counts = { positive: 0, neutral: 0, negative: 0, uncertain: 0 };
  const weighted = { positive: 0, neutral: 0, negative: 0 };
  for (const event of data || []) {
    counts[event.short_term_impact as keyof typeof counts] += 1;
    if (event.short_term_impact !== "uncertain") {
      weighted[event.short_term_impact as keyof typeof weighted] += Number(event.market_relevance || 0);
    }
  }
  const { error: upsertError } = await supabase.from("news_daily_sentiment").upsert({
    event_date: eventDate,
    positive_count: counts.positive,
    neutral_count: counts.neutral,
    negative_count: counts.negative,
    uncertain_count: counts.uncertain,
    included_event_count: counts.positive + counts.neutral + counts.negative,
    weighted_positive: weighted.positive,
    weighted_neutral: weighted.neutral,
    weighted_negative: weighted.negative,
    generated_at: new Date().toISOString(),
  });
  if (upsertError) throw upsertError;
}

async function getLookbackHours(request: Request) {
  const body = await request.json().catch(() => ({}));
  const requested = Number(body.lookback_hours ?? DEFAULT_LOOKBACK_HOURS);
  if (!Number.isInteger(requested) || requested < 1 || requested > MAX_LOOKBACK_HOURS) {
    throw new Error(`lookback_hours는 1에서 ${MAX_LOOKBACK_HOURS} 사이의 정수여야 합니다.`);
  }
  return requested;
}

async function verifyPipelineSecret(request: Request) {
  const expected = Deno.env.get("NEWS_PIPELINE_SECRET");
  if (!expected) throw new Error("NEWS_PIPELINE_SECRET이 설정되지 않았습니다.");
  const provided = request.headers.get("x-news-pipeline-secret");
  return Boolean(provided && provided === expected);
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    if (!(await verifyPipelineSecret(request))) return json({ error: "인증되지 않은 파이프라인 요청입니다." }, 401);
    const lookbackHours = await getLookbackHours(request);
    const { candidates, errors } = await collectCandidates(lookbackHours);
    const events = await analyzeCandidates(candidates);
    const persisted = await persistAnalysis(events, candidates);
    return json({
      collected: candidates.length,
      lookback_hours: lookbackHours,
      analyzed: events.length,
      persisted,
      sources: candidates.reduce<Record<string, number>>((counts, item) => {
        counts[item.source] = (counts[item.source] || 0) + 1;
        return counts;
      }, {}),
      errors,
      next_step: "분석 결과 저장 완료",
    });
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : "뉴스 수집에 실패했습니다." }, 500);
  }
});
