import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const headers = {
  "Access-Control-Allow-Origin": "https://hoorash4.github.io",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Content-Type": "application/json; charset=utf-8",
};

const respond = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers });

function dateRange(cycle: string) {
  const now = new Date();
  const yyyy = now.getUTCFullYear();
  const mm = String(now.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(now.getUTCDate()).padStart(2, "0");

  if (cycle === "D") {
    const before = new Date(now.getTime() - 45 * 86400000);
    return {
      start: `${before.getUTCFullYear()}${String(before.getUTCMonth() + 1).padStart(2, "0")}${String(before.getUTCDate()).padStart(2, "0")}`,
      end: `${yyyy}${mm}${dd}`,
    };
  }
  if (cycle === "M") return { start: `${yyyy - 2}01`, end: `${yyyy}${mm}` };
  if (cycle === "Q") return { start: `${yyyy - 5}Q1`, end: `${yyyy}Q4` };
  if (cycle === "S") return { start: `${yyyy - 8}S1`, end: `${yyyy}S2` };
  if (cycle === "A") return { start: String(yyyy - 15), end: String(yyyy) };
  throw new Error(`지원하지 않는 ECOS 주기입니다: ${cycle}`);
}

async function collect(target: any) {
  const config = target.source_config || {};
  if (target.source_type === "fred") {
    const key = Deno.env.get("FRED_API_KEY");
    const seriesId = String(config.series_id || "").trim().toUpperCase();
    if (!key || !seriesId) throw new Error("FRED API 설정이 없습니다.");
    const url = new URL("https://api.stlouisfed.org/fred/series/observations");
    url.search = new URLSearchParams({ series_id: seriesId, api_key: key, file_type: "json", sort_order: "desc", limit: "10" }).toString();
    const response = await fetch(url);
    if (!response.ok) throw new Error(`FRED API 오류 (${response.status})`);
    const row = ((await response.json()).observations || []).find((item: any) => item.value && item.value !== ".");
    if (!row) throw new Error("FRED 최신값을 찾지 못했습니다.");
    return Number(row.value);
  }
  if (target.source_type === "ecos") {
    const key = Deno.env.get("ECOS_API_KEY");
    const stat = String(config.stat_code || "").trim().toUpperCase();
    const item = String(config.item_code || "").trim();
    const cycle = String(config.data_cycle || "D").trim().toUpperCase();
    if (!key || !stat) throw new Error("ECOS API 설정이 없습니다.");
    const range = dateRange(cycle);
    const parts = ["https://ecos.bok.or.kr/api/StatisticSearch", encodeURIComponent(key), "json", "kr", "1", "100", encodeURIComponent(stat), cycle, range.start, range.end];
    if (item) parts.push(encodeURIComponent(item));
    const url = parts.join("/");
    const response = await fetch(url);
    if (!response.ok) throw new Error(`ECOS API 오류 (${response.status})`);
    const rows = (await response.json()).StatisticSearch?.row || [];
    const row = [...rows].reverse().find((entry: any) => entry.DATA_VALUE !== undefined && entry.DATA_VALUE !== "");
    if (!row) throw new Error("ECOS 최신값을 찾지 못했습니다.");
    return Number(String(row.DATA_VALUE).replaceAll(",", ""));
  }
  throw new Error("지원하지 않는 데이터 소스입니다.");
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers });
  try {
    const jwt = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
    if (!jwt) return respond({ error: "로그인이 필요합니다." }, 401);
    const url = Deno.env.get("SUPABASE_URL")!;
    const anonKey = Deno.env.get("SUPABASE_ANON_KEY")!;
    const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
    const auth = createClient(url, anonKey);
    const { data: userData, error: userError } = await auth.auth.getUser(jwt);
    if (userError || !userData.user) return respond({ error: "로그인이 필요합니다." }, 401);
    const targetId = String((await request.json()).target_id || "");
    const db = createClient(url, serviceKey);
    const { data: target, error: targetError } = await db.from("targets").select("*").eq("id", targetId).eq("user_id", userData.user.id).maybeSingle();
    if (targetError) throw targetError;
    if (!target) return respond({ error: "해당 지표를 찾을 수 없습니다." }, 404);
    const value = await collect(target);
    const { data: updated, error: updateError } = await db
      .from("targets")
      .update({ last_value: value, last_checked_at: new Date().toISOString(), last_error: null })
      .eq("id", targetId)
      .eq("user_id", userData.user.id)
      .select()
      .single();
    if (updateError) throw updateError;
    return respond({ target: updated });
  } catch (error) {
    return respond({ error: error instanceof Error ? error.message : "현재값을 확인하지 못했습니다." }, 400);
  }
});
