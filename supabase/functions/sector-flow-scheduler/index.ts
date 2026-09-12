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

function cronMatchesNow(schedule: unknown, now: Date) {
  const fields = String(schedule || "").trim().split(/\s+/);
  if (fields.length !== 5 || fields[2] !== "*" || fields[3] !== "*") return false;
  const minute = Number(fields[0]), hour = Number(fields[1]);
  if (!Number.isInteger(minute) || !Number.isInteger(hour)) return false;
  const weekday = now.getUTCDay();
  const weekdayAllowed = fields[4] === "*" || fields[4] === String(weekday)
    || (fields[4] === "1-5" && weekday >= 1 && weekday <= 5);
  return weekdayAllowed && hour === now.getUTCHours() && minute === now.getUTCMinutes();
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);

  try {
    const body = await request.json().catch(() => ({}));
    const stage = String(body?.stage || "") as PriceStage;
    if (!STAGES.has(stage)) return json({ error: "유효한 sector-flow stage가 필요합니다." }, 400);

    const now = new Date();
    const kst = kstParts(now);
    const policy = schedulePolicy(`sector-flow-${stage}.yml`);
    if (policy?.allowedWeekdays && !policy.allowedWeekdays.includes(kst.weekday)) {
      return json({ ok: true, skipped: "outside_allowed_weekday", stage });
    }
    if (policy?.earliestSafeTimeKst && kst.minute < timeMinutes(policy.earliestSafeTimeKst)) {
      return json({ ok: true, skipped: "before_safe_time", stage, earliest_safe_time_kst: policy.earliestSafeTimeKst });
    }

    const supabaseUrl = Deno.env.get("SUPABASE_URL");
    const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceKey) throw new Error("Supabase 서버 설정이 없습니다.");

    const admin = createClient(supabaseUrl, serviceKey);
    const { data: scheduleRows, error: scheduleError } = await admin.rpc("macrowatch_sector_flow_schedules");
    if (scheduleError) throw scheduleError;
    const configuredJobs = (Array.isArray(scheduleRows) ? scheduleRows : []).filter((row) => (
      String(row?.jobname || "").startsWith(`macrowatch-sector-flow-${stage}-`) && row?.active === true
    ));
    if (!configuredJobs.some((row) => cronMatchesNow(row?.schedule, now))) {
      return json({ ok: true, skipped: "outside_configured_schedule", stage });
    }

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

    const { data: result, error: invokeError } = await admin.functions.invoke("sector-flow", { body: { stage } });
    if (invokeError) throw invokeError;
    return json({ ok: true, stage, result: result ?? null });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json({ ok: false, error: message }, 500);
  }
});
