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
  leadershipScore: number | null;
  priceStage: "open" | "intraday" | "close";
};

export type SectorPriceDate = { etfId: string; marketDate: string };
export type MarketPrice = { marketDate: string; closePrice: number };

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

/**
 * 최근 20거래일이 실제 상승 흐름일 때만, KOSPI 상승일에 반복적으로
 * 초과수익을 낸 정도를 섹터 간 0~100으로 정규화합니다.
 */
export function calculateSectorLeadership(
  prices: SectorPrice[], marketPrices: MarketPrice[], endpointDate: string,
) {
  const market = marketPrices
    .filter((row) => row.marketDate <= endpointDate && row.closePrice > 0)
    .sort((a, b) => a.marketDate.localeCompare(b.marketDate))
    .slice(-21);
  if (market.length < 21) return new Map<string, number>();

  const marketReturns = market.slice(1).map((row, index) => ({
    marketDate: row.marketDate,
    previousDate: market[index].marketDate,
    value: row.closePrice / market[index].closePrice - 1,
    block: Math.floor(index / 5),
  }));
  const upDays = marketReturns.filter((row) => row.value > 0);
  const marketCumulative = market[market.length - 1].closePrice / market[0].closePrice - 1;
  if (upDays.length < 11 || marketCumulative <= 0) return new Map<string, number>();

  const byEtf = new Map<string, Map<string, number>>();
  prices.filter((row) => row.closePrice !== null && row.closePrice > 0 && row.marketDate <= endpointDate).forEach((row) => {
    const history = byEtf.get(row.etfId) || new Map<string, number>();
    history.set(row.marketDate, Number(row.closePrice));
    byEtf.set(row.etfId, history);
  });

  const rawScores = new Map<string, number>();
  for (const [etfId, history] of byEtf) {
    const excessByUpDay: Array<{ value: number; block: number }> = [];
    let complete = true;
    for (const marketReturn of marketReturns) {
      const previous = history.get(marketReturn.previousDate), current = history.get(marketReturn.marketDate);
      if (!previous || !current) {
        complete = false;
        break;
      }
      if (marketReturn.value > 0) {
        excessByUpDay.push({ value: current / previous - 1 - marketReturn.value, block: marketReturn.block });
      }
    }
    if (!complete || excessByUpDay.length !== upDays.length) continue;
    const positive = excessByUpDay.filter((row) => row.value > 0);
    if (!positive.length) {
      rawScores.set(etfId, 0);
      continue;
    }
    const strength = positive.reduce((sum, row) => sum + row.value, 0) / positive.length;
    const hitRate = positive.length / excessByUpDay.length;
    const activeBlocks = new Set(positive.map((row) => row.block)).size;
    const representedBlocks = new Set(excessByUpDay.map((row) => row.block)).size;
    const persistence = hitRate * (representedBlocks ? activeBlocks / representedBlocks : 0);
    rawScores.set(etfId, strength * persistence);
  }

  const positiveScores = [...rawScores.values()].filter((value) => value > 0).sort((a, b) => a - b);
  if (!positiveScores.length) return new Map([...rawScores.keys()].map((etfId) => [etfId, 0]));
  const reference = positiveScores[Math.max(0, Math.ceil(positiveScores.length * .9) - 1)];
  return new Map([...rawScores].map(([etfId, raw]) => [etfId, Math.round(Math.min(100, raw / reference * 100))]));
}

export function calculateSectorRankings(
  prices: SectorPrice[], currentDate: string, marketPrices: MarketPrice[] = [],
): SectorRanking[] {
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
    const weekEnd = isoDate(dateValue(week) + 6 * DAY_MS);
    const leadership = calculateSectorLeadership(prices, marketPrices, weekEnd < currentDate ? weekEnd : currentDate);
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
        weeklyReturnPct: row.weekly, cumulativeReturnPct: row.cumulative,
        leadershipScore: leadership.get(row.etfId) ?? null, priceStage: row.stage,
      });
    });
  });
  return output;
}
