export type SectorPrice = {
  etfId: string;
  marketDate: string;
  openPrice: number;
  closePrice: number | null;
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
  priceStage: "open" | "close";
};

const DAY_MS = 86_400_000;
const dateValue = (value: string) => Date.parse(`${value}T00:00:00Z`);
const isoDate = (value: number) => new Date(value).toISOString().slice(0, 10);

export function mondayOf(value: string) {
  const date = new Date(`${value}T00:00:00Z`);
  const offset = (date.getUTCDay() + 6) % 7;
  return isoDate(date.getTime() - offset * DAY_MS);
}

function effectivePrice(row: SectorPrice, currentDate: string) {
  if (row.marketDate === currentDate && row.closePrice === null) return row.openPrice;
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
  const rawByWeek = new Map<string, Array<{ etfId: string; weekly: number; cumulative: number; stage: "open" | "close" }>>();

  for (const weekStart of weekStarts) {
    const weekEnd = isoDate(dateValue(weekStart) + 6 * DAY_MS);
    const rows = [];
    for (const [etfId, history] of byEtf) {
      const baseline = [...history].reverse().find((item) => item.marketDate < weekStart && item.closePrice !== null);
      const endpoint = [...history].reverse().find((item) => item.marketDate <= weekEnd && item.marketDate <= currentDate && effectivePrice(item, currentDate) !== null);
      const latest = [...history].reverse().find((item) => item.marketDate <= currentDate && effectivePrice(item, currentDate) !== null);
      if (!baseline?.closePrice || !endpoint || !latest) continue;
      const endpointPrice = effectivePrice(endpoint, currentDate), latestPrice = effectivePrice(latest, currentDate);
      if (!endpointPrice || !latestPrice || endpoint.marketDate < weekStart) continue;
      rows.push({
        etfId,
        weekly: (endpointPrice / baseline.closePrice - 1) * 100,
        cumulative: (latestPrice / baseline.closePrice - 1) * 100,
        stage: endpoint.marketDate === currentDate && endpoint.closePrice === null ? "open" as const : "close" as const,
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
