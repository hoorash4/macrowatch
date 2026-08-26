import type { ArticleSentiment, Candidate, ExtremeNewsRule } from "./news-types.ts";
import { AI_POLICY } from "./ai-policy.ts";
import type { MarketContext } from "./market-indicators.ts";

const SENTIMENTS = ["positive", "neutral", "negative", "uncertain"] as const;

// OpenAI의 strict structured output으로 JSON 문법과 필수 필드를 보장한다.
// 필드 간 조건부 의미는 normalizeOutput에서 한 번 더 강제한다.
const ARTICLE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    outputs: {
      type: "array",
      items: {
        type: "object",
        additionalProperties: false,
        properties: {
          item_hash: { type: "string" },
          exclude_from_index: { type: "boolean" },
          sentiment: { type: "string", enum: SENTIMENTS },
          keywords: { type: "array", items: { type: "string" }, maxItems: 3 },
          uncertain_summary: { anyOf: [{ type: "string" }, { type: "null" }] },
          extreme_signal: { anyOf: [{ type: "string", enum: ["decisive"] }, { type: "null" }] },
          extreme_keywords: { type: "array", items: { type: "string" }, maxItems: 3 },
        },
        required: [
          "item_hash",
          "exclude_from_index",
          "sentiment",
          "keywords",
          "uncertain_summary",
          "extreme_signal",
          "extreme_keywords",
        ],
      },
    },
  },
  required: ["outputs"],
};

class AnalysisFormatError extends Error {}

function criteriaText(extremeRules: ExtremeNewsRule[]) {
  if (!extremeRules.length) return "- 등록된 기준 없음: 모든 기사에서 extreme_signal=null";
  return extremeRules
    .map((rule, index) => `${index + 1}. ${JSON.stringify(rule.phrase.replace(/\s+/g, " ").trim())}`)
    .join("\n");
}

function systemPrompt(extremeRules: ExtremeNewsRule[]) {
  const prompt = Deno.env.get("NEWS_ANALYSIS_SYSTEM_PROMPT");
  if (!prompt) throw new Error("NEWS_ANALYSIS_SYSTEM_PROMPT가 설정되지 않았습니다.");

  const criteria = criteriaText(extremeRules);
  const withoutLegacyCandidates = prompt.replace(/\{\{news_candidates\}\}/gi, "").trim();
  if (withoutLegacyCandidates.includes("{{EXTREME_SIGNAL_CRITERIA}}")) {
    return withoutLegacyCandidates.replace(/\{\{EXTREME_SIGNAL_CRITERIA\}\}/g, criteria);
  }

  // 구버전 프롬프트도 동작하게 하되 판단 규칙을 중복해서 덧붙이지 않는다.
  return `${withoutLegacyCandidates}\n\n## [관리자 등록 기준]\n${criteria}`;
}

function candidatePrompt(candidates: Candidate[], marketContext: MarketContext | null) {
  return JSON.stringify({
    market_context: marketContext,
    news_candidates: candidates.map(({ source, itemHash, publishedAt, text }) => ({
      source,
      item_hash: itemHash,
      published_at: publishedAt,
      text,
    })),
  });
}

function outputText(payload: Record<string, unknown>) {
  if (typeof payload.output_text === "string") return payload.output_text;
  const output = Array.isArray(payload.output) ? payload.output : [];
  for (const item of output) {
    if (!item || typeof item !== "object") continue;
    const content = Array.isArray((item as { content?: unknown[] }).content)
      ? (item as { content: unknown[] }).content
      : [];
    for (const part of content) {
      if (part && typeof part === "object" && (part as { type?: unknown }).type === "output_text"
        && typeof (part as { text?: unknown }).text === "string") {
        return (part as { text: string }).text;
      }
    }
  }
  return null;
}

function cleanKeywords(value: unknown) {
  return Array.isArray(value)
    ? value.map(String).map((keyword) => keyword.trim()).filter(Boolean).slice(0, 3)
    : [];
}

function parseOutput(item: Record<string, unknown>): ArticleSentiment {
  const sentiment = SENTIMENTS.includes(item.sentiment as typeof SENTIMENTS[number])
    ? item.sentiment as ArticleSentiment["sentiment"]
    : "uncertain";
  const extremeSignal = item.extreme_signal === "decisive" ? "decisive" : null;
  return {
    itemHash: String(item.item_hash),
    excludeFromIndex: item.exclude_from_index === true,
    sentiment,
    keywords: cleanKeywords(item.keywords),
    uncertainSummary: item.uncertain_summary === null ? null : String(item.uncertain_summary).trim() || null,
    extremeSignal,
    extremeKeywords: extremeSignal ? cleanKeywords(item.extreme_keywords) : [],
  };
}

// 제외 기사는 감성 집계 대상이 아니므로 neutral은 JSON 형식 유지를 위한 자리값일 뿐이다.
// 모델이 조건부 필드를 잘못 채워도 저장 전에 항상 동일한 조합으로 정규화한다.
function normalizeOutput(output: ArticleSentiment): ArticleSentiment {
  const normalized = {
    ...output,
    extremeKeywords: output.extremeSignal ? output.extremeKeywords : [],
  };
  if (normalized.excludeFromIndex) {
    return { ...normalized, sentiment: "neutral", keywords: [], uncertainSummary: null };
  }
  if (normalized.sentiment !== "uncertain") {
    return { ...normalized, keywords: [], uncertainSummary: null };
  }
  return normalized;
}

async function requestAnalysis(
  model: string,
  candidates: Candidate[],
  marketContext: MarketContext | null,
  extremeRules: ExtremeNewsRule[],
) {
  const apiKey = Deno.env.get("OPENAI_API_KEY");
  if (!apiKey) throw new Error("OPENAI_API_KEY가 설정되지 않았습니다.");
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      reasoning: { effort: "low" },
      max_output_tokens: 3_000,
      prompt_cache_key: "macrowatch-article-sentiment-v10",
      input: [
        { role: "system", content: [{ type: "input_text", text: systemPrompt(extremeRules) }] },
        { role: "user", content: [{ type: "input_text", text: candidatePrompt(candidates, marketContext) }] },
      ],
      text: { format: { type: "json_schema", name: "article_sentiment", strict: true, schema: ARTICLE_SCHEMA } },
    }),
  });
  if (!response.ok) throw new Error(`OpenAI 분석 오류 (${response.status}): ${await response.text()}`);

  const payload = await response.json() as Record<string, unknown>;
  const text = outputText(payload);
  if (!text) throw new AnalysisFormatError("OpenAI 응답에 output_text가 없습니다.");
  try {
    const parsed = JSON.parse(text) as { outputs?: Array<Record<string, unknown>> };
    if (!Array.isArray(parsed.outputs)) throw new AnalysisFormatError("outputs 배열이 없습니다.");
    return parsed.outputs.map(parseOutput);
  } catch (error) {
    if (error instanceof AnalysisFormatError) throw error;
    throw new AnalysisFormatError(`OpenAI JSON 해석 실패: ${error instanceof Error ? error.message : String(error)}`);
  }
}

async function analyzeOne(candidate: Candidate, marketContext: MarketContext | null, extremeRules: ExtremeNewsRule[]) {
  const output = await requestAnalysis(AI_POLICY.standardModel, [candidate], marketContext, extremeRules);
  if (output.length !== 1) throw new AnalysisFormatError("기사별 분석 결과가 하나가 아닙니다.");
  return normalizeOutput({ ...output[0], itemHash: candidate.itemHash });
}

export async function analyzeCandidates(
  candidates: Candidate[],
  marketContext: MarketContext | null,
  extremeRules: ExtremeNewsRule[],
) {
  if (!candidates.length) return [];

  let outputs: ArticleSentiment[];
  try {
    outputs = await requestAnalysis(AI_POLICY.standardModel, candidates, marketContext, extremeRules);
  } catch (error) {
    // 네트워크·인증 오류에는 무의미한 반복 호출을 하지 않는다.
    // 구조화 출력 형식만 깨진 경우에 한해 기사별로 한 번씩 복구한다.
    if (!(error instanceof AnalysisFormatError) || candidates.length === 1) throw error;
    return Promise.all(candidates.map((candidate) => analyzeOne(candidate, marketContext, extremeRules)));
  }

  const expectedHashes = new Set(candidates.map((candidate) => candidate.itemHash));
  const byHash = new Map<string, ArticleSentiment>();
  for (const output of outputs) {
    if (expectedHashes.has(output.itemHash) && !byHash.has(output.itemHash)) byHash.set(output.itemHash, output);
  }

  // 잘못된 해시나 중복 응답 때문에 정상 기사까지 다시 호출하지 않고 누락 기사만 복구한다.
  const missing = candidates.filter((candidate) => !byHash.has(candidate.itemHash));
  const recovered = await Promise.all(missing.map((candidate) => analyzeOne(candidate, marketContext, extremeRules)));
  recovered.forEach((output) => byHash.set(output.itemHash, output));

  return candidates.map((candidate) => {
    const output = byHash.get(candidate.itemHash);
    if (!output) throw new AnalysisFormatError("뉴스 분석 결과가 누락되었습니다.");
    return normalizeOutput(output);
  });
}
