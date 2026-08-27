export type KoreaFlowRaw = { observationDate: string; foreignNetBuyAmount: number; kospiTradingValue: number; usdkrwRate: number };

function mean(values: number[]) { return values.reduce((sum, value) => sum + value, 0) / values.length; }
function zScore(value: number, history: number[]) {
  if (history.length < 60) return null;
  const average = mean(history), variance = mean(history.map((item) => (item - average) ** 2));
  return variance > 0 ? Math.max(-3, Math.min(3, (value - average) / Math.sqrt(variance))) : 0;
}

// 각 시점에서 과거 정보만 사용해 3년(756영업일) 이동 Z점수를 만들므로 미래값 누출이 없습니다.
export function calculateKoreaForeignFlow(rows: KoreaFlowRaw[]) {
  const sorted = [...rows].sort((a, b) => a.observationDate.localeCompare(b.observationDate));
  const ratios: number[] = [], wonMoves: number[] = [];
  return sorted.flatMap((row, index) => {
    const ratio = row.kospiTradingValue > 0 ? row.foreignNetBuyAmount / row.kospiTradingValue : Number.NaN;
    const previousRate = index ? sorted[index - 1].usdkrwRate : Number.NaN;
    const wonMove = Number.isFinite(previousRate) && previousRate > 0 ? -(row.usdkrwRate / previousRate - 1) : Number.NaN;
    ratios.push(ratio); wonMoves.push(wonMove);
    if (!Number.isFinite(ratio) || !Number.isFinite(wonMove)) return [];
    const flowZ = zScore(ratio, ratios.slice(Math.max(0, index - 755), index + 1).filter(Number.isFinite));
    const wonZ = zScore(wonMove, wonMoves.slice(Math.max(0, index - 755), index + 1).filter(Number.isFinite));
    if (flowZ === null || wonZ === null) return [];
    return [{ observation_date: row.observationDate, foreign_net_buy_amount: row.foreignNetBuyAmount,
      kospi_trading_value: row.kospiTradingValue, foreign_flow_ratio: ratio, usdkrw_rate: row.usdkrwRate,
      usdkrw_return: -wonMove, foreign_flow_z: flowZ, won_strength_z: wonZ,
      flow_index: (flowZ + wonZ) / 2, updated_at: new Date().toISOString() }];
  });
}
