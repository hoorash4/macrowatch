import type { AnalyzedEvent, Candidate } from "./news-types.ts";
import { AI_POLICY, selectedModel } from "./ai-policy.ts";

function systemPrompt() {
  const prompt = Deno.env.get("NEWS_ANALYSIS_SYSTEM_PROMPT");
  if (!prompt) throw new Error("NEWS_ANALYSIS_SYSTEM_PROMPT가 설정되지 않았습니다.");
  return prompt.replace(/\{\{news_candidates\}\}/g, "").trim();
}

const EVENT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    events: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        properties: {
          event_date: { type: "string" },
          event_at: { anyOf: [{ type: "string" }, { type: "null" }] },
          summary: { type: "string" },
          category: { type: "string", enum: ["macro", "finance", "international"] },
          impact_scope: { type: "string", enum: ["company", "industry", "market", "systemic"] },
          transmission_channels: { type: "array", items: { type: "string" } },
          market_relevance: { type: "number", minimum: 0, maximum: 1 },
          short_term_impact: { type: "string", enum: ["positive", "neutral", "negative", "uncertain"] },
          five_day_impact: { type: "string", enum: ["positive", "neutral", "negative", "uncertain"] },
          confidence: { type: "number", minimum: 0, maximum: 1 },
          source_item_hashes: { type: "array", items: { type: "string" } },
        },
        required: [
          "event_date", "event_at", "summary", "category", "impact_scope",
          "transmission_channels", "market_relevance", "short_term_impact",
          "five_day_impact", "confidence", "source_item_hashes",
        ],
      },
    },
  },
  required: ["events"],
};

function candidatePrompt(candidates: Candidate[]) {
  return JSON.stringify(candidates.map(({ source, itemHash, publishedAt, text }) => ({
    source,
    item_hash: itemHash,
    published_at: publishedAt,
    text,
  })));
}

async function requestAnalysis(model: string, candidates: Candidate[]) {
  const apiKey = Deno.env.get("OPENAI_API_KEY");
  if (!apiKey) throw new Error("OPENAI_API_KEY가 설정되지 않았습니다.");

  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      prompt_cache_key: "macrowatch-news-analysis-v1",
      input: [
        { role: "system", content: [{ type: "input_text", text: systemPrompt() }] },
        {
          role: "user",
          content: [{ type: "input_text", text: candidatePrompt(candidates) }],
        },
      ],
      text: { format: { type: "json_schema", name: "news_event_analysis", strict: true, schema: EVENT_SCHEMA } },
    }),
  });
  if (!response.ok) throw new Error(`OpenAI 분석 오류 (${response.status}): ${await response.text()}`);
  const payload = await response.json();
  const outputText = payload.output_text;
  if (typeof outputText !== "string") throw new Error("OpenAI 응답에 output_text가 없습니다.");
  const parsed = JSON.parse(outputText) as { events: Array<Record<string, unknown>> };
  return {
    events: parsed.events.map((event) => ({
      eventDate: String(event.event_date),
      eventAt: event.event_at === null ? null : String(event.event_at),
      summary: String(event.summary),
      category: event.category as AnalyzedEvent["category"],
      impactScope: event.impact_scope as AnalyzedEvent["impactScope"],
      transmissionChannels: Array.isArray(event.transmission_channels)
        ? event.transmission_channels.map(String)
        : [],
      marketRelevance: Number(event.market_relevance),
      shortTermImpact: event.short_term_impact as AnalyzedEvent["shortTermImpact"],
      fiveDayImpact: event.five_day_impact as AnalyzedEvent["fiveDayImpact"],
      confidence: Number(event.confidence),
      sourceItemHashes: Array.isArray(event.source_item_hashes)
        ? event.source_item_hashes.map(String)
        : [],
    })),
  };
}

export async function analyzeCandidates(candidates: Candidate[]) {
  if (!candidates.length) return [];
  const firstPass = await requestAnalysis(AI_POLICY.standardModel, candidates);
  const needsReview = firstPass.events.some((event) => selectedModel({
    confidence: event.confidence,
    marketRelevance: event.market_relevance,
    impactScope: event.impact_scope,
    shortTermImpact: event.short_term_impact,
    fiveDayImpact: event.five_day_impact,
  }) === AI_POLICY.reviewModel);
  if (!needsReview) return firstPass.events;
  return (await requestAnalysis(AI_POLICY.reviewModel, candidates)).events;
}
