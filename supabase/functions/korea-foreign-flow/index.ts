import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { jsonResponse as json } from "../_shared/http.ts";
import { createClient, type SupabaseClient } from "https://esm.sh/@supabase/supabase-js@2";
import { fetchKisKospiForeignNetBuy, fetchKisKospiMarketDays, getKisAccessToken, loadKisCredentials } from "../_shared/market/kis-client.ts";
import { calculateKoreaForeignFlow, type KoreaFlowRaw } from "../_shared/market/korea-foreign-flow.ts";

const WAIT_MS = 350, CALCULATION_YEARS = 8;
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const compact = (value: string) => value.replaceAll("-", "");

async function fetchFx(start: string, end: string): Promise<Map<string, number>> {
  const key = Deno.env.get("ECOS_API_KEY")?.trim();
  if (!key) throw new Error("ECOS_API_KEY가 설정되지 않았습니다.");
  const url = `https://ecos.bok.or.kr/api/StatisticSearch/${encodeURIComponent(key)}/json/kr/1/1000/731Y001/D/${compact(start)}/${compact(end)}/0000001`;
  const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  const payload = await response.json();
  if (!response.ok || payload.RESULT) throw new Error(`ECOS 원/달러 환율 조회 실패: ${payload.RESULT?.MESSAGE || response.status}`);
  const entries: Array<[string, number]> = (payload.StatisticSearch?.row || []).map((row: Record<string, unknown>) => {
    const date = String(row.TIME || ""), value = Number(row.DATA_VALUE);
    return [`${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`, value] as [string, number];
  }).filter((entry: [string, number]) => Number.isFinite(entry[1]));
  return new Map(entries);
}

function latestFxOnOrBefore(values: Map<string, number>, marketDate: string) {
  return [...values.entries()].filter(([date]) => date <= marketDate).sort(([a], [b]) => b.localeCompare(a))[0]?.[1];
}

async function loadRawHistory(admin: SupabaseClient, start: string) {
  const byDate = new Map<string, Record<string, number>>();
  const definitions = [["KR_FOREIGN_NET_BUY", "foreign_net_buy_amount"],
    ["KOSPI_TRADING_VALUE", "kospi_trading_value"], ["USDKRW", "usdkrw_rate"]] as const;
  for (const [seriesCode, field] of definitions) {
    for (let offset = 0;; offset += 1000) {
      const { data, error } = await admin.from("economic_chart_points")
        .select("observation_date,value").eq("series_code", seriesCode)
        .gte("observation_date", start).order("observation_date").range(offset, offset + 999);
      if (error) throw error;
      for (const row of data || []) byDate.set(String(row.observation_date), {
        ...(byDate.get(String(row.observation_date)) || {}), [field]: Number(row.value),
      });
      if ((data || []).length < 1000) break;
    }
  }
  return [...byDate].filter(([, row]) => definitions.every(([, field]) => Number.isFinite(row[field])))
    .map(([observation_date, row]) => ({ observation_date, ...row }))
    .sort((a, b) => a.observation_date.localeCompare(b.observation_date));
}

function dateYearsAgo(years: number) {
  const date = new Date(Date.now() + 9 * 3_600_000); date.setUTCFullYear(date.getUTCFullYear() - years);
  return date.toISOString().slice(0, 10);
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const body = await request.json().catch(() => ({})) as Record<string, unknown>;
    const collectOnly = body.collect_only === true, finalizeOnly = body.finalize_only === true;
    const today = new Date(Date.now() + 9 * 3_600_000).toISOString().slice(0, 10);
    const start = typeof body.start === "string" ? body.start : new Date(Date.now() - 10 * 86_400_000).toISOString().slice(0, 10);
    const end = typeof body.end === "string" ? body.end : today;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || start > end) return json({ error: "조회 날짜가 올바르지 않습니다." }, 400);
    const supabaseUrl = Deno.env.get("SUPABASE_URL"), serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");
    const admin = createClient(supabaseUrl, serviceRole);
    const rawRows: KoreaFlowRaw[] = [], failures: Array<{ date: string; error: string }> = [];

    if (!finalizeOnly) {
      const credentials = loadKisCredentials(), token = await getKisAccessToken(credentials, admin);
      const marketDays = await fetchKisKospiMarketDays(credentials, token, new Date(`${start}T00:00:00Z`), new Date(`${end}T00:00:00Z`));
      const fx = await fetchFx(new Date(Date.parse(`${start}T00:00:00Z`) - 7 * 86_400_000).toISOString().slice(0, 10), end);
      const { data: existing, error: existingError } = await admin.from("economic_chart_points")
        .select("observation_date").eq("series_code", "KR_FOREIGN_NET_BUY")
        .gte("observation_date", start).lte("observation_date", end);
      if (existingError) throw existingError;
      const existingDates = new Set((existing || []).map((row) => String(row.observation_date)));
      for (const day of marketDays.sort((a, b) => a.marketDate.localeCompare(b.marketDate))) {
        if (existingDates.has(day.marketDate)) continue;
        try {
          const amount = await fetchKisKospiForeignNetBuy(credentials, token, day.marketDate);
          const rate = latestFxOnOrBefore(fx, day.marketDate);
          if (amount !== null && typeof rate === "number" && Number.isFinite(rate)) rawRows.push({ observationDate: day.marketDate, foreignNetBuyAmount: amount, kospiTradingValue: day.tradingValue, usdkrwRate: rate });
        } catch (error) { failures.push({ date: day.marketDate, error: error instanceof Error ? error.message : String(error) }); }
        await wait(WAIT_MS);
      }
      if (rawRows.length) {
        const { error } = await admin.rpc("store_korea_foreign_flow_sources", { p_rows: rawRows });
        if (error) throw error;
      }
      if (collectOnly) return json({ ok: true, start, end, collected: rawRows.length, stored: 0, failures });
    }

    // The long history is calculation context only. Automatic execution may
    // publish new dates in the requested window, but it never rewrites older
    // calculated rows merely because backend calculation code changed.
    const history = await loadRawHistory(admin, dateYearsAgo(CALCULATION_YEARS));
    const calculated = calculateKoreaForeignFlow(history.map((row) => ({ observationDate: String(row.observation_date),
      foreignNetBuyAmount: Number(row.foreign_net_buy_amount), kospiTradingValue: Number(row.kospi_trading_value), usdkrwRate: Number(row.usdkrw_rate) })));
    const { data: existingCalculated, error: existingCalculatedError } = await admin.from("korea_foreign_flow_daily")
      .select("observation_date").gte("observation_date", start).lte("observation_date", end);
    if (existingCalculatedError) throw existingCalculatedError;
    const existingCalculatedDates = new Set((existingCalculated || []).map((row) => String(row.observation_date)));
    const publishable = calculated.filter((row) =>
      row.observation_date >= start && row.observation_date <= end && !existingCalculatedDates.has(row.observation_date)
    ).map((row) => ({ observation_date: row.observation_date, foreign_flow_ratio: row.foreign_flow_ratio,
      usdkrw_return: row.usdkrw_return, foreign_flow_z: row.foreign_flow_z,
      won_strength_z: row.won_strength_z, flow_index: row.flow_index, updated_at: row.updated_at }));
    if (publishable.length) {
      const { error } = await admin.from("korea_foreign_flow_daily").insert(publishable);
      if (error) throw error;
    }

    // Retention and historical rebuild are deliberately not part of automatic
    // collection. Those are explicit maintenance operations, not side effects.
    return json({ ok: true, start, end, collected: rawRows.length, calculated: calculated.length, stored: publishable.length, failures });
  } catch (error) { return json({ error: error instanceof Error ? error.message : String(error) }, 500); }
});
