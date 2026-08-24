export type MarketCandle = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
};

export type MarketContext = {
  index: "KOSPI";
  market_date: string;
  close: number;
  ma60: number;
  disparity60: number;
  adx14: number;
  stochastic_k_10_5_5: number;
  stochastic_d_10_5_5: number;
  disparity60_widening: boolean;
  bearish_stochastic_divergence: boolean;
};

const average = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length;
const round = (value: number) => Math.round(value * 100) / 100;

function smooth(values: number[], period: number) {
  return values.map((_, index) => index + 1 < period ? null : average(values.slice(index + 1 - period, index + 1)));
}

function adx14(candles: MarketCandle[]) {
  const period = 14;
  const trueRanges: number[] = [], plusMoves: number[] = [], minusMoves: number[] = [];
  for (let index = 1; index < candles.length; index += 1) {
    const current = candles[index], previous = candles[index - 1];
    const upMove = current.high - previous.high, downMove = previous.low - current.low;
    trueRanges.push(Math.max(current.high - current.low, Math.abs(current.high - previous.close), Math.abs(current.low - previous.close)));
    plusMoves.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusMoves.push(downMove > upMove && downMove > 0 ? downMove : 0);
  }
  if (trueRanges.length < period * 2) return null;
  let tr = average(trueRanges.slice(0, period)), plus = average(plusMoves.slice(0, period)), minus = average(minusMoves.slice(0, period));
  const dx: number[] = [];
  for (let index = period; index < trueRanges.length; index += 1) {
    tr = (tr * (period - 1) + trueRanges[index]) / period;
    plus = (plus * (period - 1) + plusMoves[index]) / period;
    minus = (minus * (period - 1) + minusMoves[index]) / period;
    const plusDi = tr === 0 ? 0 : 100 * plus / tr, minusDi = tr === 0 ? 0 : 100 * minus / tr;
    dx.push(plusDi + minusDi === 0 ? 0 : 100 * Math.abs(plusDi - minusDi) / (plusDi + minusDi));
  }
  if (dx.length < period) return null;
  let adx = average(dx.slice(0, period));
  for (let index = period; index < dx.length; index += 1) adx = (adx * (period - 1) + dx[index]) / period;
  return adx;
}

function stochastic(candles: MarketCandle[]) {
  const rawK = candles.map((candle, index) => {
    if (index < 9) return null;
    const window = candles.slice(index - 9, index + 1), highest = Math.max(...window.map((item) => item.high)), lowest = Math.min(...window.map((item) => item.low));
    return highest === lowest ? 50 : 100 * (candle.close - lowest) / (highest - lowest);
  });
  const slowK = smooth(rawK.filter((value): value is number => value !== null), 5);
  const slowD = smooth(slowK.filter((value): value is number => value !== null), 5);
  const k = slowK.at(-1), d = slowD.at(-1);
  return k === null || d === null || k === undefined || d === undefined ? null : { k, d, slowK };
}

function bearishDivergence(candles: MarketCandle[], slowK: Array<number | null>) {
  const start = Math.max(1, candles.length - 30), peaks: number[] = [];
  for (let index = start; index < candles.length - 1; index += 1) {
    const k = slowK[index - 9];
    if (k !== null && k !== undefined && candles[index].close >= candles[index - 1].close && candles[index].close >= candles[index + 1].close) peaks.push(index);
  }
  if (peaks.length < 2) return false;
  const previous = peaks.at(-2)!, latest = peaks.at(-1)!;
  const previousK = slowK[previous - 9], latestK = slowK[latest - 9];
  return previousK !== null && previousK !== undefined && latestK !== null && latestK !== undefined && candles[latest].close > candles[previous].close && latestK < previousK;
}

export function calculateMarketContext(candles: MarketCandle[]): MarketContext | null {
  const ordered = [...candles].sort((left, right) => left.date.localeCompare(right.date));
  if (ordered.length < 60) return null;
  const latest = ordered.at(-1)!, ma60 = average(ordered.slice(-60).map((item) => item.close)), disparity60 = 100 * latest.close / ma60;
  const previousDisparity = ordered.length < 65 ? null : 100 * ordered.at(-6)!.close / average(ordered.slice(-65, -5).map((item) => item.close));
  const adx = adx14(ordered), stochasticValues = stochastic(ordered);
  if (adx === null || !stochasticValues) return null;
  return {
    index: "KOSPI", market_date: latest.date, close: round(latest.close), ma60: round(ma60), disparity60: round(disparity60), adx14: round(adx),
    stochastic_k_10_5_5: round(stochasticValues.k), stochastic_d_10_5_5: round(stochasticValues.d),
    disparity60_widening: previousDisparity !== null && disparity60 > previousDisparity,
    bearish_stochastic_divergence: bearishDivergence(ordered, stochasticValues.slowK),
  };
}
