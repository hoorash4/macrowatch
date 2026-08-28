export type SectorPrice = {
  etfId: string;
  marketDate: string;
  openPrice: number;
  closePrice: number | null;
  latestPrice: number;
  priceStage: "open" | "intraday" | "close";
};

export type SectorRanking = {
  weekStart: string;
  etfId: string;
  rank: number;
  previousRank: number | null;
  isNew: boolean;
  top10Streak: number;
  weeklyReturnPct: number;
  cumulativeReturnPct: number;
  priceStage: "open" | "intraday" | "close";
};

export type SectorPriceDate = { etfId: string; marketDate: string };

const DAY_MS = 86_400_000;
const dateValue = (value: string) => Date.parse(`${value}T00:00:00Z`);
const isoDate = (value: number) => new Date(value).toISOString().slice(0, 10);

export function mondayOf(value: string) {
  const date = new Date(`${value}T00:00:00Z`);
  const offset = (date.getUTCDay() + 6) % 7;
  return isoDate(date.getTime() - offset * DAY_MS);
}

/**
 * 같은 국내 거래소 ETF들이 공유하는 거래일 달력을 기준으로 이력이 덜 채워진
 * 종목을 찾습니다. 신규 상장 ETF도 한동안 재조회될 수 있지만, 호출 횟수는
 * 그대로이고 KIS가 돌려주는 실제 상장 이후 자료만 멱등 upsert됩니다.
 */
export function incompletePriceHistoryIds(etfIds: string[], prices: SectorPriceDate[]) {
  const referenceDates = new Set(prices.map((row) => row.marketDate));
  if (!referenceDates.size) return new Set(etfIds);
  const datesByEtf = new Map<string, Set<string>>();
  prices.forEach((row) => {
    const dates = datesByEtf.get(row.etfId) || new Set<string>();
    dates.add(row.marketDate);
    datesByEtf.set(row.etfId, dates);
  });
  return new Set(etfIds.filter((etfId) => {
    const dates = datesByEtf.get(etfId);
    if (!dates) return true;
    for (const marketDate of referenceDates) if (!dates.has(marketDate)) return true;
    return false;
  }));
}

function effectivePrice(row: SectorPrice, currentDate: string) {
  if (row.marketDate === currentDate && row.closePrice === null) return row.latestPrice;
  return row.closePrice;
}

export function calculateSectorRankings(prices: SectorPrice[], currentDate: string): SectorRanking[] {
  const byEtf = new Map<string, SectorPrice[]>();
  for (const row of prices) {
    const list = byEtf.get(row.etfId) || [];
    list.push(row);
    byEtf.set(row.etfId, list);
  }
  for (const list of byEtf.values()) list.sort((a, b) => a.marketDate.localeCompare(b.marketDate));

  const currentWeek = mondayOf(currentDate);
  const weekStarts = Array.from({ length: 14 }, (_, index) => isoDate(dateValue(currentWeek) - (13 - index) * 7 * DAY_MS));
  const rawByWeek = new Map<string, Array<{ etfId: string; weekly: number; cumulative: number; stage: "open" | "intraday" | "close" }>>();

  for (const weekStart of weekStarts) {
    const weekEnd = isoDate(dateValue(weekStart) + 6 * DAY_MS);
    const fourWeekStart = isoDate(dateValue(weekStart) - 3 * 7 * DAY_MS);
    const rows = [];
    for (const [etfId, history] of byEtf) {
      const baseline = [...history].reverse().find((item) => item.marketDate < weekStart && item.closePrice !== null);
      const fourWeekBaseline = [...history].reverse().find((item) => item.marketDate < fourWeekStart && item.closePrice !== null);
      const endpoint = [...history].reverse().find((item) => item.marketDate <= weekEnd && item.marketDate <= currentDate && effectivePrice(item, currentDate) !== null);
      if (!baseline?.closePrice || !fourWeekBaseline?.closePrice || !endpoint) continue;
      const endpointPrice = effectivePrice(endpoint, currentDate);
      if (!endpointPrice || endpoint.marketDate < weekStart) continue;
      rows.push({
        etfId,
        weekly: (endpointPrice / baseline.closePrice - 1) * 100,
        cumulative: (endpointPrice / fourWeekBaseline.closePrice - 1) * 100,
        stage: endpoint.marketDate === currentDate ? endpoint.priceStage : "close" as const,
      });
    }
    rows.sort((a, b) => b.weekly - a.weekly || a.etfId.localeCompare(b.etfId));
    rawByWeek.set(weekStart, rows);
  }

  const ranked = new Map<string, Map<string, number>>();
  for (const [week, rows] of rawByWeek) ranked.set(week, new Map(rows.map((row, index) => [row.etfId, index + 1])));
  const output: SectorRanking[] = [];
  weekStarts.forEach((week, weekIndex) => {
    const previous = weekIndex ? ranked.get(weekStarts[weekIndex - 1]) : null;
    (rawByWeek.get(week) || []).forEach((row, index) => {
      const rank = index + 1, previousRank = previous?.get(row.etfId) ?? null;
      let streak = 0;
      for (let cursor = weekIndex; cursor >= 0; cursor--) {
        const historicalRank = ranked.get(weekStarts[cursor])?.get(row.etfId);
        if (!historicalRank || historicalRank > 10) break;
        streak++;
      }
      output.push({
        weekStart: week, etfId: row.etfId, rank, previousRank,
        isNew: rank <= 10 && (previousRank === null || previousRank > 10),
        top10Streak: rank <= 10 ? streak : 0,
        weeklyReturnPct: row.weekly, cumulativeReturnPct: row.cumulative, priceStage: row.stage,
      });
    });
  });
  return output;
}
