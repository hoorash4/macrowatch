import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { fetchKisKospiForeignNetBuy, fetchKisKospiMarketDays, getKisAccessToken, loadKisCredentials } from "../_shared/kis-client.ts";
import { calculateKoreaForeignFlow, type KoreaFlowRaw } from "../_shared/korea-foreign-flow.ts";

const WAIT_MS = 350, RETENTION_YEARS = 5, CALCULATION_YEARS = 8;
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const compact = (value: string) => value.replaceAll("-", "");
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });

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

async function loadRawHistory(admin: ReturnType<typeof createClient>, start: string) {
  const rows: Record<string, unknown>[] = [];
  for (let offset = 0;; offset += 1000) {
    const { data, error } = await admin.from("korea_foreign_flow_raw")
      .select("observation_date,foreign_net_buy_amount,kospi_trading_value,usdkrw_rate")
      .gte("observation_date", start).order("observation_date").range(offset, offset + 999);
    if (error) throw error;
    rows.push(...(data || []));
    if ((data || []).length < 1000) break;
  }
  return rows;
}

function dateYearsAgo(years: number) {
  const date = new Date(Date.now() + 9 * 3_600_000); date.setUTCFullYear(date.getUTCFullYear() - years);
  return date.toISOString().slice(0, 10);
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    const body = await request.json().catch(() => ({})) as Record<string, unknown>;
    const today = new Date(Date.now() + 9 * 3_600_000).toISOString().slice(0, 10);
    const start = typeof body.start === "string" ? body.start : new Date(Date.now() - 10 * 86_400_000).toISOString().slice(0, 10);
    const end = typeof body.end === "string" ? body.end : today;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || start > end) return json({ error: "조회 날짜가 올바르지 않습니다." }, 400);
    const supabaseUrl = Deno.env.get("SUPABASE_URL"), serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");
    const admin = createClient(supabaseUrl, serviceRole), credentials = loadKisCredentials();
    const token = await getKisAccessToken(credentials, admin);
    const marketDays = await fetchKisKospiMarketDays(credentials, token, new Date(`${start}T00:00:00Z`), new Date(`${end}T00:00:00Z`));
    const fx = await fetchFx(new Date(Date.parse(`${start}T00:00:00Z`) - 7 * 86_400_000).toISOString().slice(0, 10), end);
    const { data: existing, error: existingError } = await admin.from("korea_foreign_flow_raw")
      .select("observation_date").gte("observation_date", start).lte("observation_date", end);
    if (existingError) throw existingError;
    const existingDates = new Set((existing || []).map((row) => String(row.observation_date)));
    const rawRows: KoreaFlowRaw[] = [], failures: Array<{ date: string; error: string }> = [];
    for (const day of marketDays.sort((a, b) => a.marketDate.localeCompare(b.marketDate))) {
      // 중단된 장기 백필을 재개할 때 이미 저장된 영업일은 외부 API를 다시 호출하지 않습니다.
      if (existingDates.has(day.marketDate)) continue;
      try {
        const amount = await fetchKisKospiForeignNetBuy(credentials, token, day.marketDate);
        // 환율 휴일과 거래소 영업일이 어긋나면 가장 최근 공시 환율을 이어 사용합니다.
        const rate = latestFxOnOrBefore(fx, day.marketDate);
        if (amount !== null && typeof rate === "number" && Number.isFinite(rate)) rawRows.push({ observationDate: day.marketDate, foreignNetBuyAmount: amount, kospiTradingValue: day.tradingValue, usdkrwRate: rate });
      } catch (error) { failures.push({ date: day.marketDate, error: error instanceof Error ? error.message : String(error) }); }
      await wait(WAIT_MS);
    }
    if (rawRows.length) {
      const { error } = await admin.from("korea_foreign_flow_raw").upsert(rawRows.map((row) => ({
        observation_date: row.observationDate, foreign_net_buy_amount: row.foreignNetBuyAmount,
        kospi_trading_value: row.kospiTradingValue, usdkrw_rate: row.usdkrwRate, updated_at: new Date().toISOString(),
      })), { onConflict: "observation_date" });
      if (error) throw error;
    }
    const history = await loadRawHistory(admin, dateYearsAgo(CALCULATION_YEARS));
    const calculated = calculateKoreaForeignFlow(history.map((row) => ({ observationDate: String(row.observation_date),
      foreignNetBuyAmount: Number(row.foreign_net_buy_amount), kospiTradingValue: Number(row.kospi_trading_value), usdkrwRate: Number(row.usdkrw_rate) })));
    if (calculated.length) {
      const { error } = await admin.from("korea_foreign_flow_daily").upsert(calculated.filter((row) => row.observation_date >= dateYearsAgo(RETENTION_YEARS)), { onConflict: "observation_date" });
      if (error) throw error;
    }
    await admin.from("korea_foreign_flow_daily").delete().lt("observation_date", dateYearsAgo(RETENTION_YEARS));
    await admin.from("korea_foreign_flow_raw").delete().lt("observation_date", dateYearsAgo(CALCULATION_YEARS));
    return json({ ok: true, start, end, collected: rawRows.length, calculated: calculated.length, failures });
  } catch (error) { return json({ error: error instanceof Error ? error.message : String(error) }, 500); }
});
