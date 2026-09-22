import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
  AI_MODEL_ALERT_SETTINGS_KEY,
  alertableAiModels,
  availableAiModels,
  configuredAiModel,
} from "../_shared/policy/ai-model-selection.ts";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST only" }, 405);
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!serviceRoleKey || request.headers.get("Authorization") !== `Bearer ${serviceRoleKey}`) {
    return json({ error: "Unauthorized" }, 401);
  }

  try {
    const body = await request.json();
    const action = String(body?.action || "");
    if (action !== "scan" && action !== "report") return json({ error: "Invalid action" }, 400);
    const url = Deno.env.get("SUPABASE_URL");
    if (!url) throw new Error("SUPABASE_URL is missing");
    const admin = createClient(url, serviceRoleKey, {
      auth: { persistSession: false, autoRefreshToken: false },
    });
    const { data, error } = await admin.from("app_settings")
      .select("value").eq("key", AI_MODEL_ALERT_SETTINGS_KEY).maybeSingle();
    if (error) throw error;
    const status = data?.value && typeof data.value === "object" && !Array.isArray(data.value)
      ? data.value as Record<string, unknown> : {};
    const save = async (value: Record<string, unknown>) => {
      const { error: saveError } = await admin.from("app_settings").upsert({
        key: AI_MODEL_ALERT_SETTINGS_KEY,
        value,
        updated_at: new Date().toISOString(),
      }, { onConflict: "key" });
      if (saveError) throw saveError;
    };

    if (action === "scan") {
      const apiKey = Deno.env.get("OPENAI_API_KEY");
      if (!apiKey) throw new Error("OPENAI_API_KEY is missing");
      const [fomc, standard, models] = await Promise.all([
        configuredAiModel(admin, "fomc"), configuredAiModel(admin, "standard"), availableAiModels(apiKey),
      ]);
      const notified = Array.isArray(status.notified_ids)
        ? status.notified_ids.filter((id): id is string => typeof id === "string") : [];
      const modelIds = alertableAiModels(models, { fomc, standard }, notified);
      await save({ ...status, last_checked_at: new Date().toISOString(), pending_ids: modelIds });
      return json({ model_ids: modelIds, fomc, standard });
    }

    const modelIds = body?.model_ids;
    const pending = status.pending_ids;
    if (!Array.isArray(modelIds) || !modelIds.length || !modelIds.every((id) => typeof id === "string")
      || !Array.isArray(pending) || modelIds.length !== pending.length
      || modelIds.some((id, index) => id !== pending[index]) || typeof body?.success !== "boolean") {
      return json({ error: "Invalid email report" }, 400);
    }
    const success = body.success as boolean;
    const notified = Array.isArray(status.notified_ids)
      ? status.notified_ids.filter((id): id is string => typeof id === "string") : [];
    await save({
      ...status,
      pending_ids: [],
      notified_ids: success ? [...new Set([...notified, ...modelIds])] : notified,
      last_email_at: new Date().toISOString(),
      last_email_success: success,
      last_email_models: modelIds,
    });
    return json({ ok: true });
  } catch (error) {
    console.error("[ai-model-monitor]", error);
    return json({ error: error instanceof Error ? error.message : String(error) }, 500);
  }
});
