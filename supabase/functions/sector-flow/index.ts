import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { fetchKisDailyPrices, issueKisAccessToken, loadKisCredentials } from "../_shared/kis-client.ts";
import { calculateSectorRankings, mondayOf, type SectorPrice } from "../_shared/sector-flow.ts";

const HISTORY_DAYS = 105;
const REQUEST_INTERVAL_MS = 180;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
}

function kstDate() {
  return new Date(Date.now() + 9 * 3_600_000).toISOString().slice(0, 10);
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    // 배포 기본값인 Supabase 게이트웨이 JWT 검증을 통과한 서버 요청만 여기까지 도달한다.
    const body = await request.json().catch(() => ({})) as Record<string, unknown>;
    const stage = body.stage === "open" ? "open" : body.stage === "close" ? "close" : null;
    if (!stage) return json({ error: "stage는 open 또는 close여야 합니다." }, 400);

    const supabaseUrl = Deno.env.get("SUPABASE_URL"), serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");
    const admin = createClient(supabaseUrl, serviceRole);
    const { data: registry, error: registryError } = await admin.from("market_sector_etfs")
      .select("id,etf_ticker,sector_name").order("sector_name");
    if (registryError) throw registryError;
    if (!registry?.length) throw new Error("등록된 섹터 ETF가 없습니다.");

    const credentials = loadKisCredentials(), token = await issueKisAccessToken(credentials);
    const today = kstDate(), end = new Date(`${today}T00:00:00Z`);
    const start = new Date(end.getTime() - HISTORY_DAYS * 86_400_000);
    const collected: Record<string, unknown>[] = [], failures: Array<{ ticker: string; error: string }> = [];

    for (const item of registry) {
      try {
        const candles = await fetchKisDailyPrices(credentials, token, item.etf_ticker, start, end);
        for (const candle of candles) {
          const isToday = candle.marketDate === today;
          collected.push({
            etf_id: item.id,
            market_date: candle.marketDate,
            open_price: candle.open,
            close_price: isToday && stage === "open" ? null : candle.close,
            volume: candle.volume,
            updated_at: new Date().toISOString(),
          });
        }
      } catch (error) {
        failures.push({ ticker: item.etf_ticker, error: error instanceof Error ? error.message : String(error) });
      }
      await new Promise((resolve) => setTimeout(resolve, REQUEST_INTERVAL_MS));
    }
    if (!collected.length) throw new Error("수집된 ETF 가격이 없습니다.");

    const { error: priceError } = await admin.from("market_sector_etf_prices")
      .upsert(collected, { onConflict: "etf_id,market_date" });
    if (priceError) throw priceError;

    const historyStart = new Date(end.getTime() - HISTORY_DAYS * 86_400_000).toISOString().slice(0, 10);
    const { data: prices, error: historyError } = await admin.from("market_sector_etf_prices")
      .select("etf_id,market_date,open_price,close_price").gte("market_date", historyStart).order("market_date");
    if (historyError) throw historyError;
    const normalized: SectorPrice[] = (prices || []).map((row) => ({
      etfId: row.etf_id, marketDate: row.market_date, openPrice: Number(row.open_price),
      closePrice: row.close_price === null ? null : Number(row.close_price),
    }));
    const rankings = calculateSectorRankings(normalized, today);
    const currentWeek = mondayOf(today);
    const replaceFrom = new Date(Date.parse(`${currentWeek}T00:00:00Z`) - 13 * 7 * 86_400_000).toISOString().slice(0, 10);
    const { error: deleteError } = await admin.from("market_sector_weekly_rankings").delete().gte("week_start", replaceFrom);
    if (deleteError) throw deleteError;
    if (rankings.length) {
      const { error: rankingError } = await admin.from("market_sector_weekly_rankings").insert(rankings.map((row) => ({
        week_start: row.weekStart, etf_id: row.etfId, rank: row.rank, previous_rank: row.previousRank,
        is_new: row.isNew, top10_streak: row.top10Streak, weekly_return_pct: row.weeklyReturnPct,
        cumulative_return_pct: row.cumulativeReturnPct, price_stage: row.priceStage,
        calculated_at: new Date().toISOString(),
      })));
      if (rankingError) throw rankingError;
    }
    return json({ ok: true, stage, registry_count: registry.length, price_rows: collected.length, ranking_rows: rankings.length, failures });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json({ ok: false, error: message }, 500);
  }
});
