export const AI_POLICY = Object.freeze({
  provider: "openai",
  promptVersion: Deno.env.get("NEWS_ANALYSIS_PROMPT_VERSION") || "v1",
  standardModel: Deno.env.get("AI_MODEL_STANDARD") || "gpt-5.6-luna",
});
