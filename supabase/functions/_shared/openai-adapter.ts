import type { ArticleSentiment, Candidate } from "./news-types.ts";
import { AI_POLICY } from "./ai-policy.ts";
import type { MarketContext } from "./market-indicators.ts";

const ARTICLE_SCHEMA = { type: "object", additionalProperties: false, properties: { outputs: { type: "array", items: { type: "object", additionalProperties: false, properties: { item_hash: { type: "string" }, exclude_from_index: { type: "boolean" }, sentiment: { type: "string", enum: ["positive", "neutral", "negative", "uncertain"] }, keywords: { type: "array", items: { type: "string" } }, uncertain_summary: { anyOf: [{ type: "string" }, { type: "null" }] } }, required: ["item_hash", "exclude_from_index", "sentiment", "keywords", "uncertain_summary"] } } }, required: ["outputs"] };

function systemPrompt() {
  const prompt = Deno.env.get("NEWS_ANALYSIS_SYSTEM_PROMPT");
  if (!prompt) throw new Error("NEWS_ANALYSIS_SYSTEM_PROMPT가 설정되지 않았습니다.");
  return prompt.replace(/\{\{news_candidates\}\}/g, "").trim();
}

function candidatePrompt(candidates: Candidate[], marketContext: MarketContext | null) {
  return JSON.stringify({ market_context: marketContext, news_candidates: candidates.map(({ source, itemHash, publishedAt, text }) => ({ source, item_hash: itemHash, published_at: publishedAt, text })) });
}

async function requestAnalysis(model: string, candidates: Candidate[], marketContext: MarketContext | null) {
  const apiKey = Deno.env.get("OPENAI_API_KEY");
  if (!apiKey) throw new Error("OPENAI_API_KEY가 설정되지 않았습니다.");
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST", headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model, reasoning: { effort: "low" }, max_output_tokens: 3_000, prompt_cache_key: "macrowatch-article-sentiment-v2", input: [{ role: "system", content: [{ type: "input_text", text: systemPrompt() }] }, { role: "user", content: [{ type: "input_text", text: candidatePrompt(candidates, marketContext) }] }], text: { format: { type: "json_schema", name: "article_sentiment", strict: true, schema: ARTICLE_SCHEMA } } }),
  });
  if (!response.ok) throw new Error(`OpenAI 분석 오류 (${response.status}): ${await response.text()}`);
  const payload = await response.json();
  const outputText = typeof payload.output_text === "string" ? payload.output_text : payload.output?.flatMap((item: { content?: Array<{ type?: string; text?: string }> }) => item.content || []).find((item: { type?: string }) => item.type === "output_text")?.text;
  if (typeof outputText !== "string") throw new Error("OpenAI 응답에 output_text가 없습니다.");
  const parsed = JSON.parse(outputText) as { outputs: Array<Record<string, unknown>> };
  return parsed.outputs.map((item): ArticleSentiment => ({ itemHash: String(item.item_hash), excludeFromIndex: item.exclude_from_index === true, sentiment: item.sentiment as ArticleSentiment["sentiment"], keywords: Array.isArray(item.keywords) ? item.keywords.map(String).slice(0, 3) : [], uncertainSummary: item.uncertain_summary === null ? null : String(item.uncertain_summary) }));
}

export async function analyzeCandidates(candidates: Candidate[], marketContext: MarketContext | null) {
  if (!candidates.length) return [];
  const outputs = await requestAnalysis(AI_POLICY.standardModel, candidates, marketContext);
  const expected = new Set(candidates.map((candidate) => candidate.itemHash));
  const received = outputs.map((item) => item.itemHash);
  const uniqueReceived = new Set(received);
  if (received.length !== uniqueReceived.size || [...uniqueReceived].some((hash) => !expected.has(hash))) {
    throw new Error("AI 분석 결과의 뉴스 식별자가 올바르지 않습니다.");
  }
  const missing = candidates.filter((candidate) => !uniqueReceived.has(candidate.itemHash));
  if (missing.length) {
    const recovered = await Promise.all(missing.map(async (candidate) => {
      const single = await requestAnalysis(AI_POLICY.standardModel, [candidate], marketContext);
      if (single.length !== 1 || single[0].itemHash !== candidate.itemHash) throw new Error("누락 뉴스 재분석 결과의 식별자가 올바르지 않습니다.");
      return single[0];
    }));
    outputs.push(...recovered);
  }
  return candidates.map((candidate) => {
    const output = outputs.find((item) => item.itemHash === candidate.itemHash);
    if (!output) throw new Error("뉴스 분석 결과가 누락되었습니다.");
    return output.excludeFromIndex || output.sentiment === "uncertain" ? output : { ...output, keywords: [], uncertainSummary: null };
  });
}
