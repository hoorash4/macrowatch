import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { fetchKisDailyPrices, issueKisAccessToken, loadKisCredentials } from "../_shared/kis-client.ts";
import { calculateSectorRankings, mondayOf, type SectorPrice } from "../_shared/sector-flow.ts";

const REQUEST_INTERVAL_MS = 180;
const DATABASE_PAGE_SIZE = 1000;
const RETENTION_WEEKS = 9;
type StoredSectorPrice = { etf_id: string; market_date: string; open_price: number | string; close_price: number | string | null };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
}

function kstDate() {
  return new Date(Date.now() + 9 * 3_600_000).toISOString().slice(0, 10);
}

// Supabase Data API의 기본 행 제한을 넘는 가격 이력도 빠짐없이 읽습니다.
async function loadSectorPriceHistory(admin: ReturnType<typeof createClient>, historyStart: string) {
  const allRows: StoredSectorPrice[] = [];
  for (let from = 0;; from += DATABASE_PAGE_SIZE) {
    const { data, error } = await admin.from("market_sector_etf_prices")
      .select("etf_id,market_date,open_price,close_price")
      .gte("market_date", historyStart)
      .order("market_date", { ascending: true })
      .order("etf_id", { ascending: true })
      .range(from, from + DATABASE_PAGE_SIZE - 1);
    if (error) throw error;
    const page = (data || []) as StoredSectorPrice[];
    allRows.push(...page);
    if (page.length < DATABASE_PAGE_SIZE) break;
  }
  return allRows;
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
    const currentWeek = mondayOf(today);
    const retentionStart = new Date(Date.parse(`${currentWeek}T00:00:00Z`) - (RETENTION_WEEKS - 1) * 7 * 86_400_000)
      .toISOString().slice(0, 10);
    const collected: Record<string, unknown>[] = [], failures: Array<{ ticker: string; error: string }> = [];

    for (const item of registry) {
      try {
        // 초기 이력은 이미 적재되어 있으므로 운영 중에는 당일 일봉만 갱신합니다.
        const candles = await fetchKisDailyPrices(credentials, token, item.etf_ticker, end, end);
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
    if (!collected.length) {
      if (failures.length) throw new Error(`ETF 가격 수집 실패: ${failures.length}건`);
      return json({ ok: true, stage, skipped: true, reason: "거래일 가격이 없습니다.", failures });
    }

    const { error: priceError } = await admin.from("market_sector_etf_prices")
      .upsert(collected, { onConflict: "etf_id,market_date" });
    if (priceError) throw priceError;

    // 종가가 정상 반영된 뒤에만 9주 범위를 벗어난 원본 가격을 정리합니다.
    if (stage === "close") {
      const { error: retentionError } = await admin.from("market_sector_etf_prices")
        .delete().lt("market_date", retentionStart);
      if (retentionError) throw retentionError;
    }

    const prices = await loadSectorPriceHistory(admin, retentionStart);
    const normalized: SectorPrice[] = prices.map((row) => ({
      etfId: row.etf_id, marketDate: row.market_date, openPrice: Number(row.open_price),
      closePrice: row.close_price === null ? null : Number(row.close_price),
    }));
    const rankings = calculateSectorRankings(normalized, today);
    const currentRows = rankings.filter((row) => row.weekStart === currentWeek);
    const previousWeek = new Date(Date.parse(`${currentWeek}T00:00:00Z`) - 7 * 86_400_000).toISOString().slice(0, 10);
    const { data: previousRows, error: previousError } = await admin.from("market_sector_weekly_rankings")
      .select("etf_id,rank,top10_streak").eq("week_start", previousWeek);
    if (previousError) throw previousError;
    const previousByEtf = new Map((previousRows || []).map((row) => [row.etf_id, row]));
    const persistedRows = currentRows.map((row) => {
      const previous = previousByEtf.get(row.etfId);
      const previousRank = previous ? Number(previous.rank) : row.previousRank;
      const top10Streak = row.rank <= 10
        ? previous && Number(previous.rank) <= 10 ? Number(previous.top10_streak) + 1 : row.top10Streak
        : 0;
      return { ...row, previousRank, top10Streak, isNew: row.rank <= 10 && (previousRank === null || previousRank > 10) };
    });
    const { error: deleteError } = await admin.from("market_sector_weekly_rankings").delete().eq("week_start", currentWeek);
    if (deleteError) throw deleteError;
    if (persistedRows.length) {
      const { error: rankingError } = await admin.from("market_sector_weekly_rankings").insert(persistedRows.map((row) => ({
        week_start: row.weekStart, etf_id: row.etfId, rank: row.rank, previous_rank: row.previousRank,
        is_new: row.isNew, top10_streak: row.top10Streak, weekly_return_pct: row.weeklyReturnPct,
        cumulative_return_pct: row.cumulativeReturnPct, price_stage: row.priceStage,
        calculated_at: new Date().toISOString(),
      })));
      if (rankingError) throw rankingError;
    }
    return json({ ok: true, stage, registry_count: registry.length, price_rows: collected.length, ranking_rows: persistedRows.length, retention_start: retentionStart, failures });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json({ ok: false, error: message }, 500);
  }
});
