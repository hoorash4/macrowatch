export const AI_POLICY = Object.freeze({
  provider: "openai",
  promptVersion: Deno.env.get("NEWS_ANALYSIS_PROMPT_VERSION") || "v1",
  standardModel: Deno.env.get("AI_MODEL_STANDARD") || "gpt-5.6-luna",
  reviewModel: Deno.env.get("AI_MODEL_REVIEW") || "gpt-5.6-terra",
  reviewConfidenceThreshold: Number(Deno.env.get("AI_REVIEW_CONFIDENCE_THRESHOLD") || "0.65"),
  reviewRelevanceThreshold: Number(Deno.env.get("AI_REVIEW_RELEVANCE_THRESHOLD") || "0.8"),
});

type AnalysisSignal = {
  confidence?: number;
  marketRelevance?: number;
  impactScope?: string;
  shortTermImpact?: string;
  fiveDayImpact?: string;
};

export function shouldEscalateToReview(signal: AnalysisSignal) {
  const confidence = Number(signal.confidence || 0);
  const relevance = Number(signal.marketRelevance || 0);
  const systemicScope = String(signal.impactScope || "") === "systemic";
  const uncertainImpact = [signal.shortTermImpact, signal.fiveDayImpact].includes("uncertain");

  return confidence < AI_POLICY.reviewConfidenceThreshold
    || relevance >= AI_POLICY.reviewRelevanceThreshold
    || systemicScope
    || uncertainImpact;
}

export function selectedModel(signal: AnalysisSignal) {
  return shouldEscalateToReview(signal) ? AI_POLICY.reviewModel : AI_POLICY.standardModel;
}
