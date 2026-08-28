import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type PriceStage = "open" | "close";

type ScheduleSlot = {
  name: string;
  stage: PriceStage;
  startMinute: number;
  endMinute: number;
};

const SLOTS: ScheduleSlot[] = [
  { name: "market-open", stage: "open", startMinute: 9 * 60 + 10, endMinute: 9 * 60 + 40 },
  { name: "midday", stage: "open", startMinute: 12 * 60 + 30, endMinute: 13 * 60 },
  { name: "market-close", stage: "close", startMinute: 15 * 60 + 40, endMinute: 16 * 60 + 10 },
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

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

function slotStartUtc(date: string, slot: ScheduleSlot) {
  const hour = Math.floor(slot.startMinute / 60);
  const minute = slot.startMinute % 60;
  return new Date(`${date}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00+09:00`);
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);

  try {
    const now = new Date();
    const kst = kstParts(now);
    if (kst.weekday === 0 || kst.weekday === 6) {
      return json({ ok: true, skipped: "weekend" });
    }

    // 공개 스케줄러 엔드포인트는 정해진 실행 창 밖에서는 어떤 작업도 하지 않는다.
    // 각 창은 본 실행과 15분 뒤 재시도를 모두 수용한다.
    const slot = SLOTS.find((candidate) => kst.minute >= candidate.startMinute && kst.minute < candidate.endMinute);
    if (!slot) return json({ ok: true, skipped: "outside_schedule_window" });

    const supabaseUrl = Deno.env.get("SUPABASE_URL");
    const serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");

    const admin = createClient(supabaseUrl, serviceRole);
    const currentWeek = mondayOf(kst.date);
    const slotStartedAt = slotStartUtc(kst.date, slot);
    const { data: latest, error: latestError } = await admin
      .from("market_sector_weekly_rankings")
      .select("calculated_at,price_stage")
      .eq("week_start", currentWeek)
      .eq("price_stage", slot.stage)
      .gte("calculated_at", slotStartedAt.toISOString())
      .order("calculated_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (latestError) throw latestError;

    // GitHub 안전망 또는 앞선 Supabase 호출이 이미 끝났다면 중복 KIS 호출을 막는다.
    if (latest) {
      return json({ ok: true, skipped: "already_refreshed", slot: slot.name, calculated_at: latest.calculated_at });
    }

    const response = await fetch(`${supabaseUrl}/functions/v1/sector-flow`, {
      method: "POST",
      headers: {
        apikey: serviceRole,
        Authorization: `Bearer ${serviceRole}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ stage: slot.stage, backfill_history: false }),
    });
    const responseText = await response.text();
    if (!response.ok) throw new Error(`sector-flow ${response.status}: ${responseText.slice(0, 500)}`);

    return json({ ok: true, slot: slot.name, stage: slot.stage, result: responseText ? JSON.parse(responseText) : null });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json({ ok: false, error: message }, 500);
  }
});
