import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { createKisRequestRunner, fetchKisDailyPrices, fetchKisEtfCurrentPrice, fetchKisEtfTopHoldings, getKisAccessToken, loadKisCredentials } from "../_shared/kis-client.ts";
import { calculateSectorRankings, incompletePriceHistoryIds, mondayOf, type SectorPrice, type SectorRanking } from "../_shared/sector-flow.ts";

const DATABASE_PAGE_SIZE = 1000;
const PRICE_RETENTION_WEEKS = 10;
const RANKING_RETENTION_WEEKS = 6;
type StoredSectorPrice = {
  etf_id: string;
  market_date: string;
  open_price: number | string;
  close_price: number | string | null;
  latest_price: number | string;
  price_stage: "open" | "intraday" | "close";
};
type StoredRankingAnchor = { etf_id: string; rank: number | string; previous_rank: number | string | null; top10_streak: number | string };

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
      .select("etf_id,market_date,open_price,close_price,latest_price,price_stage")
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

// 6주 재계산 전부터 이어진 TOP 10 기록과 직전 순위를 보관 구간의 첫 주에 연결합니다.
function stitchRebuiltRankings(rankings: SectorRanking[], anchors: StoredRankingAnchor[]) {
  const anchorByEtf = new Map(anchors.map((row) => [row.etf_id, row]));
  const weeks = [...new Set(rankings.map((row) => row.weekStart))].sort();
  let previousByEtf = new Map<string, SectorRanking>();
  const stitched: SectorRanking[] = [];
  weeks.forEach((week, weekIndex) => {
    const currentByEtf = new Map<string, SectorRanking>();
    rankings.filter((row) => row.weekStart === week).forEach((row) => {
      const anchor = weekIndex === 0 ? anchorByEtf.get(row.etfId) : null;
      const previous = previousByEtf.get(row.etfId);
      const previousRank = anchor?.previous_rank !== null && anchor?.previous_rank !== undefined
        ? Number(anchor.previous_rank)
        : previous?.rank ?? row.previousRank;
      const top10Streak = row.rank <= 10
        ? anchor && Number(anchor.rank) <= 10
          ? Number(anchor.top10_streak)
          : previous && previous.rank <= 10 ? previous.top10Streak + 1 : 1
        : 0;
      const next = { ...row, previousRank, top10Streak, isNew: row.rank <= 10 && (previousRank === null || previousRank > 10) };
      currentByEtf.set(row.etfId, next);
      stitched.push(next);
    });
    previousByEtf = currentByEtf;
  });
  return stitched;
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return json({ error: "POST 요청만 허용됩니다." }, 405);
  try {
    // 배포 기본값인 Supabase 게이트웨이 JWT 검증을 통과한 서버 요청만 여기까지 도달한다.
    const body = await request.json().catch(() => ({})) as Record<string, unknown>;
    const stage = body.stage === "open" ? "open" : body.stage === "intraday" ? "intraday" : body.stage === "close" ? "close" : null;
    const rebuildOnly = body.rebuild_only === true;
    const backfillHistory = body.backfill_history === true;
    if (!stage) return json({ error: "stage는 open, intraday 또는 close여야 합니다." }, 400);

    const supabaseUrl = Deno.env.get("SUPABASE_URL"), serviceRole = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRole) throw new Error("Supabase 서버 설정이 없습니다.");
    const admin = createClient(supabaseUrl, serviceRole);
    const { data: registry, error: registryError } = await admin.from("market_sector_etfs")
      .select("id,etf_ticker,sector_name").order("sector_name");
    if (registryError) throw registryError;
    if (!registry?.length) throw new Error("등록된 섹터 ETF가 없습니다.");

    const today = kstDate(), end = new Date(`${today}T00:00:00Z`);
    const currentWeek = mondayOf(today);
    const retentionStart = new Date(Date.parse(`${currentWeek}T00:00:00Z`) - (PRICE_RETENTION_WEEKS - 1) * 7 * 86_400_000)
      .toISOString().slice(0, 10);
    const rankingRetentionStart = new Date(Date.parse(`${currentWeek}T00:00:00Z`) - (RANKING_RETENTION_WEEKS - 1) * 7 * 86_400_000)
      .toISOString().slice(0, 10);
    const collected: Record<string, unknown>[] = [], failures: Array<{ ticker: string; error: string }> = [];
    const holdings: Record<string, unknown>[] = [], holdingRefreshIds: string[] = [];
    const holdingFailures: Array<{ ticker: string; error: string }> = [];
    // 등록 도중 KIS가 일부 일봉만 반환해도 다음 정기 수집에서 자동 복구합니다.
    // 보관 구간의 국내 거래일 달력은 정상 수집된 다른 ETF들의 합집합을 사용합니다.
    const existingPrices = await loadSectorPriceHistory(admin, retentionStart);
    const autoBackfillIds = incompletePriceHistoryIds(
      registry.map((item) => item.id),
      existingPrices.map((row) => ({ etfId: row.etf_id, marketDate: row.market_date })),
    );

    if (!rebuildOnly) {
      const credentials = loadKisCredentials(), token = await getKisAccessToken(credentials, admin);
      const runKisRequest = createKisRequestRunner();
      for (const item of registry) {
        try {
          // 정상 종목은 당일만 조회하고, 수동 백필 또는 이력 누락 종목은 10주를 다시 채웁니다.
          const needsHistoryBackfill = backfillHistory || autoBackfillIds.has(item.id);
          const priceStart = needsHistoryBackfill ? new Date(`${retentionStart}T00:00:00Z`) : end;
          const candles = await runKisRequest(() => fetchKisDailyPrices(credentials, token, item.etf_ticker, priceStart, end));
          const intradayQuote = stage === "intraday" && candles.some((candle) => candle.marketDate === today)
            ? await runKisRequest(() => fetchKisEtfCurrentPrice(credentials, token, item.etf_ticker))
            : null;
          for (const candle of candles) {
            const isToday = candle.marketDate === today;
            // 장중 수집은 일봉으로 거래일을 확인한 뒤 ETF 현재가 API 값을 사용한다.
            const latestPrice = isToday && intradayQuote ? intradayQuote.current
              : isToday && stage === "open" ? candle.open : candle.close;
            collected.push({
              etf_id: item.id,
              market_date: candle.marketDate,
              open_price: isToday && intradayQuote ? intradayQuote.open : candle.open,
              close_price: isToday && stage !== "close" ? null : candle.close,
              latest_price: latestPrice,
              price_stage: isToday ? stage : "close",
              volume: isToday && intradayQuote ? intradayQuote.volume : candle.volume,
              updated_at: new Date().toISOString(),
            });
          }
        } catch (error) {
          failures.push({ ticker: item.etf_ticker, error: error instanceof Error ? error.message : String(error) });
        }
        if (stage === "close") {
          try {
            const topHoldings = await runKisRequest(() => fetchKisEtfTopHoldings(credentials, token, item.etf_ticker, 3));
            if (topHoldings.length) {
              holdingRefreshIds.push(item.id);
              holdings.push(...topHoldings.map((holding, index) => ({
                etf_id: item.id, holding_ticker: holding.ticker, holding_name: holding.name,
                weight_pct: holding.weightPct, weight_rank: index + 1, updated_at: new Date().toISOString(),
              })));
            }
          } catch (error) {
            holdingFailures.push({ ticker: item.etf_ticker, error: error instanceof Error ? error.message : String(error) });
          }
        }
      }
      if (!collected.length) {
        if (failures.length) throw new Error(`ETF 가격 수집 실패: ${failures.length}건`);
        return json({ ok: true, stage, skipped: true, reason: "거래일 가격이 없습니다.", failures });
      }

      const { error: priceError } = await admin.from("market_sector_etf_prices")
        .upsert(collected, { onConflict: "etf_id,market_date" });
      if (priceError) throw priceError;
      if (holdingRefreshIds.length) {
        const { error: holdingDeleteError } = await admin.from("market_sector_etf_holdings").delete().in("etf_id", holdingRefreshIds);
        if (holdingDeleteError) throw holdingDeleteError;
        const { error: holdingInsertError } = await admin.from("market_sector_etf_holdings").insert(holdings);
        if (holdingInsertError) throw holdingInsertError;
      }
    }

    // 종가가 정상 반영된 뒤에만 10주 범위를 벗어난 원본 가격을 정리합니다.
    if (stage === "close" && !rebuildOnly) {
      const { error: retentionError } = await admin.from("market_sector_etf_prices")
        .delete().lt("market_date", retentionStart);
      if (retentionError) throw retentionError;
    }

    const prices = await loadSectorPriceHistory(admin, retentionStart);
    const normalized: SectorPrice[] = prices.map((row) => ({
      etfId: row.etf_id, marketDate: row.market_date, openPrice: Number(row.open_price),
      closePrice: row.close_price === null ? null : Number(row.close_price),
      latestPrice: Number(row.latest_price), priceStage: row.price_stage,
    }));
    const rankings = calculateSectorRankings(normalized, today);
    const currentRows = rankings.filter((row) => row.weekStart === currentWeek);
    const previousWeek = new Date(Date.parse(`${currentWeek}T00:00:00Z`) - 7 * 86_400_000).toISOString().slice(0, 10);
    const { data: previousRows, error: previousError } = await admin.from("market_sector_weekly_rankings")
      .select("etf_id,rank,top10_streak").eq("week_start", previousWeek);
    if (previousError) throw previousError;
    const previousByEtf = new Map((previousRows || []).map((row) => [row.etf_id, row]));
    let rebuiltRows: SectorRanking[] = [];
    if (rebuildOnly) {
      const { data: anchorRows, error: anchorError } = await admin.from("market_sector_weekly_rankings")
        .select("etf_id,rank,previous_rank,top10_streak").eq("week_start", rankingRetentionStart);
      if (anchorError) throw anchorError;
      rebuiltRows = stitchRebuiltRankings(
        rankings.filter((row) => row.weekStart >= rankingRetentionStart),
        (anchorRows || []) as StoredRankingAnchor[],
      );
    }
    const persistedRows = rebuildOnly ? rebuiltRows : currentRows.map((row) => {
      const previous = previousByEtf.get(row.etfId);
      const previousRank = previous ? Number(previous.rank) : row.previousRank;
      const top10Streak = row.rank <= 10
        ? previous && Number(previous.rank) <= 10 ? Number(previous.top10_streak) + 1 : row.top10Streak
        : 0;
      return { ...row, previousRank, top10Streak, isNew: row.rank <= 10 && (previousRank === null || previousRank > 10) };
    });
    const replaceFrom = rebuildOnly && persistedRows.length ? persistedRows[0].weekStart : currentWeek;
    const deleteQuery = admin.from("market_sector_weekly_rankings").delete();
    const { error: deleteError } = rebuildOnly
      ? await deleteQuery.gte("week_start", replaceFrom)
      : await deleteQuery.eq("week_start", currentWeek);
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
    if (stage === "close" && !rebuildOnly) {
      const { error: oldRankingError } = await admin.from("market_sector_weekly_rankings").delete().lt("week_start", rankingRetentionStart);
      if (oldRankingError) throw oldRankingError;
    }
    return json({
      ok: true, stage, rebuild_only: rebuildOnly, backfill_history: backfillHistory,
      auto_backfill_count: autoBackfillIds.size,
      registry_count: registry.length, price_rows: collected.length, holding_rows: holdings.length,
      ranking_rows: persistedRows.length, retention_start: retentionStart, failures, holding_failures: holdingFailures,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return json({ ok: false, error: message }, 500);
  }
});
