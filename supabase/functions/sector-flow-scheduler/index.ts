import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { jsonResponse as json } from "../_shared/http.ts";
import { schedulePolicy, timeMinutes } from "../_shared/schedule-policy.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type PriceStage = "open" | "intraday" | "close";

const STAGES = new Set<PriceStage>(["open", "intraday", "close"]);

function kstParts(now = new Date()) {
  const shifted = new Date(now.getTime() + 9 * 3_600_000);
  return {
    date: shifted.toISOString().slice(0, 10),
    weekday: shifted.getUTCDay(),
    minute: shifted.getUTCHours() * 60 + shifted.getUTCMinutes(),
  };
}

function mondayOf(value: string) {
  const date = new Date(`${value}T00:00:00Z`);
  const offset = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - offset);
  return date.toISOString().slice(0, 10);
}

function kstDayStartUtc(value: string) {
  return new Date(`${value}T00:00:00+09:00`);
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);

  try {
    const body = await request.json().catch(() => ({}));
    const stage = String(body?.stage || "") as PriceStage;
    if (!STAGES.has(stage)) return json({ error: "유효한 sector-flow stage가 필요합니다." }, 400);

    const now = new Date();
    const kst = kstParts(now);
    const policy = schedulePolicy(`sector-flow-${stage}`);
    if (policy?.allowedWeekdays && !policy.allowedWeekdays.includes(kst.weekday)) {
      return json({ ok: true, skipped: "outside_allowed_weekday", stage });
    }
    if (policy?.earliestSafeTimeKst && kst.minute < timeMinutes(policy.earliestSafeTimeKst)) {
      return json({ ok: true, skipped: "before_safe_time", stage, earliest_safe_time_kst: policy.earliestSafeTimeKst });
    }

    const supabaseUrl = Deno.env.get("SUPABASE_URL");
    const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");

    const admin = createClient(supabaseUrl, serviceRole);
    const currentWeek = mondayOf(kst.date);
    const { data: latest, error: latestError } = await admin
      .from("market_sector_weekly_rankings")
      .select("calculated_at,price_stage")
      .eq("week_start", currentWeek)
      .eq("price_stage", stage)
      .gte("calculated_at", kstDayStartUtc(kst.date).toISOString())
      .order("calculated_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (latestError) throw latestError;

    // The primary and retry jobs carry the same explicit stage. Once either
    // succeeds on the current Korean trading day, later retries are no-ops.
    if (latest) {
      return json({ ok: true, skipped: "already_refreshed", stage, calculated_at: latest.calculated_at });
    }

    const response = await fetch(`${supabaseUrl}/functions/v1/sector-flow`, {
      method: "POST",
      headers: {
        apikey: serviceRole,
        Authorization: `Bearer ${serviceRole}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ stage }),
    });
    const responseText = await response.text();
    if (!response.ok) throw new Error(`sector-flow ${response.status}: ${responseText.slice(0, 500)}`);

    return json({ ok: true, stage, result: responseText ? JSON.parse(responseText) : null });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json({ ok: false, error: message }, 500);
  }
});
