export const AI_POLICY = Object.freeze({
  provider: "openai",
  promptVersion: Deno.env.get("NEWS_ANALYSIS_PROMPT_VERSION") || "v1",
  standardModel: "gpt-6-luna",
});
