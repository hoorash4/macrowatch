import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { analyzeCandidates } from "../_shared/openai-adapter.ts";
import type { ArticleSentiment, Candidate, SourceName } from "../_shared/news-types.ts";

const DEFAULT_LOOKBACK_HOURS = 24;
const MAX_LOOKBACK_HOURS = 15 * 24;
const DEFAULT_BATCH_SIZE = 10;
const MAX_BATCH_SIZE = 10;
const MAX_CANDIDATE_TEXT_CHARS = 1_000;
const SOURCE_FETCH_TIMEOUT_MS = 30_000;
const RSS_REQUEST_HEADERS = { "User-Agent": "Mozilla/5.0 (compatible; MacroWatch/1.0)", Accept: "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8", "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8" };
const RSS_FEEDS: Record<SourceName, string[]> = {
  yonhap: ["https://www.yna.co.kr/rss/economy.xml", "https://www.yna.co.kr/rss/international.xml"],
  maekyung: ["https://www.mk.co.kr/rss/30100041/", "https://www.mk.co.kr/rss/30300018/", "https://www.mk.co.kr/rss/50200011/"],
};

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } }); }
function normalizeText(value: string) { return value.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim(); }
function parseDate(value: string | null) { const date = value ? new Date(value) : null; return date && !Number.isNaN(date.getTime()) ? date.toISOString() : null; }
async function hashText(value: string) { const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value.toLowerCase().replace(/\W+/g, " ").trim())); return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join(""); }
function withinLookbackWindow(date: string | null, lookbackHours: number, now = Date.now()) { const timestamp = date ? Date.parse(date) : NaN; return Number.isFinite(timestamp) && timestamp >= now - lookbackHours * 3_600_000 && timestamp <= now + 300_000; }
function candidateText(title: string, summary: string) { return normalizeText(`${title} ${summary}`).slice(0, MAX_CANDIDATE_TEXT_CHARS); }
function xmlText(value: string) { return normalizeText(value.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&#39;/g, "'")); }
function xmlTag(block: string, names: string[]) { const match = block.match(new RegExp(`<(${names.join("|")})\\b[^>]*>([\\s\\S]*?)<\\/\\1>`, "i")); return match ? xmlText(match[2]) : null; }
function xmlLink(block: string) { const content = xmlTag(block, ["link"]); const href = block.match(/<link\b[^>]*\bhref=["']([^"']+)["'][^>]*\/?>(?:<\/link>)?/i); return content || (href ? href[1] : null); }
function xmlItems(xml: string) { return (xml.match(/<(?:item|entry)\b[\s\S]*?<\/(?:item|entry)>/gi) || []).map((entry) => { const title = xmlTag(entry, ["title"]) || ""; const summary = xmlTag(entry, ["description", "summary", "content", "content:encoded"]) || ""; return { text: candidateText(title, summary), hasSummary: Boolean(summary), url: xmlLink(entry), publishedAt: parseDate(xmlTag(entry, ["pubDate", "published", "updated", "date"])) }; }); }
async function fetchRss(source: SourceName, lookbackHours: number): Promise<Candidate[]> {
  const results = await Promise.allSettled(RSS_FEEDS[source].map(async (feed) => {
    const response = await fetch(feed, { headers: RSS_REQUEST_HEADERS, signal: AbortSignal.timeout(SOURCE_FETCH_TIMEOUT_MS) });
    if (!response.ok) throw new Error(`${source} RSS 오류 (${response.status})`);
    const candidates: Candidate[] = [];
    for (const item of xmlItems(await response.text())) if (item.text && item.hasSummary && withinLookbackWindow(item.publishedAt, lookbackHours)) candidates.push({ source, itemHash: await hashText(item.text), ...item });
    return candidates;
  }));
  return results.flatMap((result) => result.status === "fulfilled" ? result.value : []);
}
function deduplicate(candidates: Candidate[]) { const seen = new Set<string>(); return candidates.filter((candidate) => !seen.has(candidate.itemHash) && Boolean(seen.add(candidate.itemHash))); }
async function collectCandidates(lookbackHours: number) {
  const sources: SourceName[] = ["yonhap", "maekyung"];
  const results = await Promise.allSettled(sources.map((source) => fetchRss(source, lookbackHours)));
  return { candidates: deduplicate(results.flatMap((result) => result.status === "fulfilled" ? result.value : [])), errors: results.flatMap((result, index) => result.status === "rejected" ? [{ source: sources[index], error: String(result.reason) }] : []) };
}
function kstDate(iso: string) { return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso)); }
async function refreshDailySentiment(supabase: ReturnType<typeof createClient>, articleDate: string) {
  const { data, error } = await supabase.from("news_article_sentiments").select("ai_sentiment,admin_sentiment").eq("article_date", articleDate);
  if (error) throw error;
  const counts = { positive: 0, neutral: 0, negative: 0, uncertain: 0 };
  for (const row of data || []) counts[(row.admin_sentiment || row.ai_sentiment) as keyof typeof counts] += 1;
  const { error: upsertError } = await supabase.from("news_daily_article_sentiment").upsert({ article_date: articleDate, positive_count: counts.positive, negative_count: counts.negative, neutral_count: counts.neutral, uncertain_count: counts.uncertain, analyzed_article_count: (data || []).length, generated_at: new Date().toISOString() });
  if (upsertError) throw upsertError;
}
async function persistAnalysis(outputs: ArticleSentiment[], candidates: Candidate[]) {
  const supabaseUrl = Deno.env.get("SUPABASE_URL"), serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) throw new Error("Supabase 서버 설정이 없습니다.");
  const candidatesByHash = new Map(candidates.map((candidate) => [candidate.itemHash, candidate]));
  const dates = new Set<string>(), now = new Date().toISOString();
  const rows = outputs.map((output) => { const candidate = candidatesByHash.get(output.itemHash); if (!candidate?.publishedAt) throw new Error("발행 시각이 없는 뉴스 후보가 있습니다."); const articleDate = kstDate(candidate.publishedAt); dates.add(articleDate); return { article_hash: output.itemHash, source_name: candidate.source, published_at: candidate.publishedAt, article_date: articleDate, ai_sentiment: output.sentiment, derived_keywords: output.keywords, uncertain_summary: output.uncertainSummary, updated_at: now }; });
  const supabase = createClient(supabaseUrl, serviceRoleKey);
  const { error } = await supabase.from("news_article_sentiments").upsert(rows, { onConflict: "article_hash" });
  if (error) throw error;
  for (const date of dates) await refreshDailySentiment(supabase, date);
  return { articles: rows.length, dates: [...dates].sort() };
}
async function getRunOptions(request: Request) { const body = await request.json().catch(() => ({})); const lookbackHours = Number(body.lookback_hours ?? DEFAULT_LOOKBACK_HOURS), offset = Number(body.offset ?? 0), limit = Number(body.limit ?? DEFAULT_BATCH_SIZE); if (!Number.isInteger(lookbackHours) || lookbackHours < 1 || lookbackHours > MAX_LOOKBACK_HOURS) throw new Error(`lookback_hours는 1에서 ${MAX_LOOKBACK_HOURS} 사이의 정수여야 합니다.`); if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > MAX_BATCH_SIZE) throw new Error(`offset은 0 이상의 정수이고 limit은 1에서 ${MAX_BATCH_SIZE} 사이여야 합니다.`); return { lookbackHours, offset, limit, dryRun: body.dry_run === true }; }
Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  let stage = "요청 검증";
  try {
    const { lookbackHours, offset, limit, dryRun } = await getRunOptions(request); stage = "뉴스 수집";
    const { candidates, errors } = await collectCandidates(lookbackHours), batch = candidates.slice(offset, offset + limit); stage = "AI 분석";
    const outputs = await analyzeCandidates(batch); stage = "결과 저장";
    const persisted = dryRun ? { articles: 0, dates: [] } : await persistAnalysis(outputs, batch);
    const sentiments = outputs.reduce<Record<string, number>>((counts, output) => ({ ...counts, [output.sentiment]: (counts[output.sentiment] || 0) + 1 }), { positive: 0, negative: 0, neutral: 0, uncertain: 0 });
    return json({ collected: candidates.length, processed: batch.length, analyzed_articles: outputs.length, sentiments, lookback_hours: lookbackHours, offset, limit, has_more: offset + batch.length < candidates.length, next_offset: offset + batch.length, dry_run: dryRun, persisted, sources: batch.reduce<Record<string, number>>((counts, item) => ({ ...counts, [item.source]: (counts[item.source] || 0) + 1 }), {}), errors, next_step: dryRun ? "테스트 완료 (저장 없음)" : "기사별 분류와 일별 집계 저장 완료" });
  } catch (error) { return json({ error: `${stage}: ${error instanceof Error ? error.message : String(error)}`, stage }, 500); }
});
