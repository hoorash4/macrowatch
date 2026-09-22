import { AI_POLICY } from "./ai-policy.ts";

export type AiModelRole = "fomc" | "standard";
export const AI_MODEL_SETTINGS_KEY = "ai_model_selection";
export const AI_MODEL_ALERT_SETTINGS_KEY = "ai_model_alert_status";

export function defaultAiModel(role: AiModelRole) {
  return role === "fomc" ? AI_POLICY.fomcModel : AI_POLICY.standardModel;
}

export function aiModelVersion(id: string, role: AiModelRole): number[] | null {
  const tier = role === "fomc" ? "sol" : "luna";
  const match = new RegExp(`^gpt-(\\d+(?:\\.\\d+)*)-${tier}$`).exec(id);
  return match ? match[1].split(".").map(Number) : null;
}

export function compareAiModelVersions(left: number[], right: number[]) {
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const difference = (left[index] || 0) - (right[index] || 0);
    if (difference) return difference;
  }
  return 0;
}

function generalGptVersion(id: string): number[] | null {
  const match = /^gpt-(\d+(?:\.\d+)*)(?:-[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)?$/.exec(id);
  return match ? match[1].split(".").map(Number) : null;
}

export function alertableAiModels(
  models: Array<{ id: string; shutdown_date?: string | null }>,
  current: { fomc: string; standard: string },
  notifiedIds: string[],
  today = new Date().toISOString().slice(0, 10),
): string[] {
  const fomcVersion = generalGptVersion(current.fomc);
  const standardVersion = generalGptVersion(current.standard);
  if (!fomcVersion || !standardVersion) throw new Error("현재 AI 모델 ID 형식을 확인해 주세요.");
  const baseline = compareAiModelVersions(fomcVersion, standardVersion) < 0 ? fomcVersion : standardVersion;
  const notified = new Set(notifiedIds);
  return [...new Set(models.filter((model) => {
    const version = generalGptVersion(model.id);
    return version && compareAiModelVersions(version, baseline) > 0
      && (!model.shutdown_date || model.shutdown_date > today)
      && model.id !== current.fomc && model.id !== current.standard
      && !notified.has(model.id);
  }).map((model) => model.id))].sort((a, b) =>
    compareAiModelVersions(generalGptVersion(b)!, generalGptVersion(a)!) || a.localeCompare(b));
}

export function selectableAiModels(models: Array<{ id: string; shutdown_date?: string | null }>, current: string, role: AiModelRole) {
  const currentVersion = aiModelVersion(current, role);
  if (!currentVersion) throw new Error("현재 AI 모델 ID 형식을 확인해 주세요.");
  const today = new Date().toISOString().slice(0, 10);
  const newer = models.filter((model) => {
    const version = aiModelVersion(model.id, role);
    return version && compareAiModelVersions(version, currentVersion) > 0
      && (!model.shutdown_date || model.shutdown_date > today);
  }).sort((a, b) => compareAiModelVersions(aiModelVersion(b.id, role)!, aiModelVersion(a.id, role)!));
  return [{ id: current, current: true }, ...newer.map(({ id }) => ({ id, current: false }))];
}

export async function configuredAiModel(client: any, role: AiModelRole): Promise<string> {
  const { data, error } = await client.from("app_settings")
    .select("value").eq("key", AI_MODEL_SETTINGS_KEY).maybeSingle();
  if (error) throw error;
  const value = data?.value?.[role];
  if (value == null) return defaultAiModel(role);
  if (typeof value !== "string" || !aiModelVersion(value, role)) {
    throw new Error("저장된 AI 모델 설정이 올바르지 않습니다.");
  }
  return value;
}

export async function availableAiModels(apiKey: string): Promise<Array<{ id: string; shutdown_date?: string | null }>> {
  const response = await fetch("https://api.openai.com/v1/models", {
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  if (!response.ok) throw new Error(`OpenAI 모델 목록 조회에 실패했습니다. (${response.status})`);
  const payload = await response.json() as { data?: Array<{ id?: unknown; shutdown_date?: unknown }> };
  if (!Array.isArray(payload.data)) throw new Error("OpenAI 모델 목록 응답이 올바르지 않습니다.");
  return payload.data.filter((model): model is { id: string; shutdown_date?: string | null } =>
    typeof model?.id === "string" && (model.shutdown_date == null || typeof model.shutdown_date === "string"));
}
