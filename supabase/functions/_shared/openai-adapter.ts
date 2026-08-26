import type { ArticleSentiment, Candidate, ExtremeNewsRule, ExtremeSignal } from "./news-types.ts";
import { AI_POLICY } from "./ai-policy.ts";
import type { MarketContext } from "./market-indicators.ts";

const ARTICLE_SCHEMA = { type: "object", additionalProperties: false, properties: { outputs: { type: "array", items: { type: "object", additionalProperties: false, properties: { item_hash: { type: "string" }, exclude_from_index: { type: "boolean" }, sentiment: { type: "string", enum: ["positive", "neutral", "negative", "uncertain"] }, keywords: { type: "array", items: { type: "string" } }, uncertain_summary: { anyOf: [{ type: "string" }, { type: "null" }] }, extreme_signal: { anyOf: [{ type: "string", enum: ["critical_negative", "critical_positive"] }, { type: "null" }] } }, required: ["item_hash", "exclude_from_index", "sentiment", "keywords", "uncertain_summary", "extreme_signal"] } } }, required: ["outputs"] };

function systemPrompt(extremeRules: ExtremeNewsRule[]) {
  const prompt = Deno.env.get("NEWS_ANALYSIS_SYSTEM_PROMPT");
  if (!prompt) throw new Error("NEWS_ANALYSIS_SYSTEM_PROMPT가 설정되지 않았습니다.");
  const base = prompt.replace(/\{\{news_candidates\}\}/g, "").trim();
  const rules = extremeRules.map((rule) => `- ${rule.signal}: ${rule.phrase}`).join("\n");
  return `${base}\n\n[미국 주식시장 파급 경로 보완]\n일반 뉴스의 exclude_from_index 및 sentiment 판단에서 한국 지수 파급 경로뿐 아니라 미국 주가지수 파급 경로도 함께 검토하라. 미국의 금리·통화정책·고용·물가·신용·대형 금융기관·핵심 산업·원자재·글로벌 위험회피·국제 공급망에 직접 연결되는 사건은 한국 지수 경로가 기사에 명시되지 않았더라도 단순히 제3국 뉴스로 제외하지 않는다. 한국과 미국의 합리적 파급 방향이 충돌하고 우열을 판단할 핵심 사실이 없으면 uncertain을 사용한다. 이 보완 규칙은 기존의 모든 기사별 출력·중복 금지·사실 제한 규칙을 바꾸지 않는다.\n\n[치명적 악재·결정적 호재 감지]\n관리자가 등록한 아래 기준은 한국 주가지수 영향 판단과 별개의 최우선 감지 기준이다. 관리자가 등록한 기준과 문맥상 실질적으로 같은 사건이면, 한국 지수와의 전달 경로가 없거나 exclude_from_index=true인 기사라도 반드시 해당 extreme_signal을 반환하라. 정확한 키워드 일치는 요구하지 않지만, 단어 일부·막연한 관련성·단순 전망만으로는 분류하지 않는다. 기사 전체의 주체·행동·원인·파급 범위를 함께 확인한다. 기준이 없거나 어느 기준에도 해당하지 않으면 null이다. 동시에 상반된 기준에 해당하면 기사 내용상 더 직접적이고 우세한 하나만 선택하고, 우열을 판단할 수 없으면 null이다. extreme_signal 판단을 위해 exclude_from_index 또는 sentiment 값을 바꾸지 마라.\n${rules || "- 등록된 기준 없음: 모든 기사 extreme_signal=null"}\n\n출력 JSON의 각 항목에 extreme_signal을 반드시 포함한다. 허용값은 critical_negative, critical_positive, null뿐이다.`;
}

function candidatePrompt(candidates: Candidate[], marketContext: MarketContext | null) {
  return JSON.stringify({ market_context: marketContext, news_candidates: candidates.map(({ source, itemHash, publishedAt, text }) => ({ source, item_hash: itemHash, published_at: publishedAt, text })) });
}

async function requestAnalysis(model: string, candidates: Candidate[], marketContext: MarketContext | null, extremeRules: ExtremeNewsRule[]) {
  const apiKey = Deno.env.get("OPENAI_API_KEY");
  if (!apiKey) throw new Error("OPENAI_API_KEY가 설정되지 않았습니다.");
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST", headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model, reasoning: { effort: "low" }, max_output_tokens: 3_000, prompt_cache_key: "macrowatch-article-sentiment-v5", input: [{ role: "system", content: [{ type: "input_text", text: systemPrompt(extremeRules) }] }, { role: "user", content: [{ type: "input_text", text: candidatePrompt(candidates, marketContext) }] }], text: { format: { type: "json_schema", name: "article_sentiment", strict: true, schema: ARTICLE_SCHEMA } } }),
  });
  if (!response.ok) throw new Error(`OpenAI 분석 오류 (${response.status}): ${await response.text()}`);
  const payload = await response.json();
  const outputText = typeof payload.output_text === "string" ? payload.output_text : payload.output?.flatMap((item: { content?: Array<{ type?: string; text?: string }> }) => item.content || []).find((item: { type?: string }) => item.type === "output_text")?.text;
  if (typeof outputText !== "string") throw new Error("OpenAI 응답에 output_text가 없습니다.");
  const parsed = JSON.parse(outputText) as { outputs: Array<Record<string, unknown>> };
  return parsed.outputs.map((item): ArticleSentiment => ({ itemHash: String(item.item_hash), excludeFromIndex: item.exclude_from_index === true, sentiment: item.sentiment as ArticleSentiment["sentiment"], keywords: Array.isArray(item.keywords) ? item.keywords.map(String).slice(0, 3) : [], uncertainSummary: item.uncertain_summary === null ? null : String(item.uncertain_summary), extremeSignal: item.extreme_signal === "critical_negative" || item.extreme_signal === "critical_positive" ? item.extreme_signal as Exclude<ExtremeSignal, null> : null }));
}

export async function analyzeCandidates(candidates: Candidate[], marketContext: MarketContext | null, extremeRules: ExtremeNewsRule[]) {
  if (!candidates.length) return [];
  let outputs = await requestAnalysis(AI_POLICY.standardModel, candidates, marketContext, extremeRules);
  const expected = new Set(candidates.map((candidate) => candidate.itemHash));
  const received = outputs.map((item) => item.itemHash);
  const uniqueReceived = new Set(received);
  if (received.length !== uniqueReceived.size || [...uniqueReceived].some((hash) => !expected.has(hash))) {
    outputs = await Promise.all(candidates.map(async (candidate) => {
      const single = await requestAnalysis(AI_POLICY.standardModel, [candidate], marketContext, extremeRules);
      if (single.length !== 1) throw new Error("기사별 재분석 결과가 하나가 아닙니다.");
      return { ...single[0], itemHash: candidate.itemHash };
    }));
  }
  const resolvedHashes = new Set(outputs.map((item) => item.itemHash));
  const missing = candidates.filter((candidate) => !resolvedHashes.has(candidate.itemHash));
  if (missing.length) {
    const recovered = await Promise.all(missing.map(async (candidate) => {
      const single = await requestAnalysis(AI_POLICY.standardModel, [candidate], marketContext, extremeRules);
      if (single.length !== 1) throw new Error("누락 뉴스 재분석 결과가 하나가 아닙니다.");
      return { ...single[0], itemHash: candidate.itemHash };
    }));
    outputs.push(...recovered);
  }
  return candidates.map((candidate) => {
    const output = outputs.find((item) => item.itemHash === candidate.itemHash);
    if (!output) throw new Error("뉴스 분석 결과가 누락되었습니다.");
    return output.excludeFromIndex || output.sentiment === "uncertain" ? output : { ...output, keywords: [], uncertainSummary: null };
  });
}
