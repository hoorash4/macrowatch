import test from "node:test";
import assert from "node:assert/strict";
import { calculateSectorLeadership } from "../supabase/functions/_shared/sector-flow.ts";

const dates = Array.from({ length: 21 }, (_, index) => {
  const date = new Date(Date.UTC(2026, 0, 1 + index));
  return date.toISOString().slice(0, 10);
});

function closes(returns: number[]) {
  const values = [100];
  returns.forEach((value) => values.push(values.at(-1)! * (1 + value)));
  return values;
}

function marketRows(returns: number[]) {
  return closes(returns).map((closePrice, index) => ({ marketDate: dates[index], closePrice }));
}

function sectorRows(etfId: string, returns: number[]) {
  return closes(returns).map((closePrice, index) => ({
    etfId, marketDate: dates[index], openPrice: closePrice, closePrice,
    latestPrice: closePrice, priceStage: "close" as const,
  }));
}

test("주도력은 시장 상승일의 강도와 20거래일 지속성을 모두 요구한다", () => {
  const marketReturns = Array.from({ length: 20 }, (_, index) => index % 5 < 3 ? .01 : -.003);
  const steady = marketReturns.map((value) => value > 0 ? value + .005 : value);
  const oneDaySpike = marketReturns.map((value, index) => value > 0 && index === 0 ? value + .05 : value);
  const defensive = marketReturns.map((value) => value < 0 ? value + .05 : value);
  const scores = calculateSectorLeadership([
    ...sectorRows("steady", steady),
    ...sectorRows("spike", oneDaySpike),
    ...sectorRows("defensive", defensive),
  ], marketRows(marketReturns), dates.at(-1)!);

  assert.equal(scores.get("steady"), 100);
  assert.ok((scores.get("spike") || 0) < (scores.get("steady") || 0));
  assert.equal(scores.get("defensive"), 0);
});

test("최근 20거래일의 상승일이 11일 미만이면 주도력을 산출하지 않는다", () => {
  const marketReturns = Array.from({ length: 20 }, (_, index) => index % 2 === 0 ? .02 : -.01);
  const scores = calculateSectorLeadership(
    sectorRows("sector", marketReturns.map((value) => value > 0 ? value + .01 : value)),
    marketRows(marketReturns), dates.at(-1)!,
  );
  assert.equal(scores.size, 0);
});

test("상승일이 많아도 시장의 20거래일 누적수익률이 음수면 산출하지 않는다", () => {
  const marketReturns = Array.from({ length: 20 }, (_, index) => index < 11 ? .001 : -.02);
  const scores = calculateSectorLeadership(
    sectorRows("sector", marketReturns.map((value) => value > 0 ? value + .01 : value)),
    marketRows(marketReturns), dates.at(-1)!,
  );
  assert.equal(scores.size, 0);
});
