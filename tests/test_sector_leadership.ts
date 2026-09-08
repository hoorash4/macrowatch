import test from "node:test";
import assert from "node:assert/strict";
import { calculateSectorLeadership } from "../supabase/functions/_shared/sector-flow.ts";

const dates = Array.from({ length: 51 }, (_, index) => {
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

test("주도력은 10주간 강도와 지속성을 함께 만족한 섹터를 높게 평가한다", () => {
  const marketReturns = Array.from({ length: 50 }, (_, index) => index % 5 < 3 ? .01 : -.003);
  const steady = marketReturns.map((value) => value > 0 ? value + .006 : value);
  const shortSpike = marketReturns.map((value, index) => value > 0 && index >= 40 ? value + .025 : value);
  const defensive = marketReturns.map((value) => value < 0 ? value + .05 : value);
  const scores = calculateSectorLeadership([
    ...sectorRows("steady", steady),
    ...sectorRows("spike", shortSpike),
    ...sectorRows("defensive", defensive),
  ], marketRows(marketReturns), dates.at(-1)!);

  assert.equal(scores.get("steady"), 100);
  assert.ok((scores.get("spike") || 0) < (scores.get("steady") || 0));
  assert.ok((scores.get("spike") || 0) < 50);
  assert.equal(scores.get("defensive"), 0);
});

test("상승일의 절반 안팎에서만 우세하면 상대적으로 강해도 낮게 평가한다", () => {
  const marketReturns = Array.from({ length: 50 }, (_, index) => index % 5 < 3 ? .01 : -.003);
  let upDayIndex = 0;
  const inconsistent = marketReturns.map((value) => {
    if (value <= 0) return value;
    upDayIndex += 1;
    return value + (upDayIndex <= 16 ? .004 : -.004);
  });
  const scores = calculateSectorLeadership(
    sectorRows("inconsistent", inconsistent), marketRows(marketReturns), dates.at(-1)!,
  );
  assert.ok((scores.get("inconsistent") || 0) < 30);
});

test("새로 등록해 이력을 백필한 섹터는 장중 최신가로도 주도력을 산출한다", () => {
  const marketReturns = Array.from({ length: 50 }, (_, index) => index % 5 < 3 ? .01 : -.003);
  const intraday = sectorRows("new-sector", marketReturns.map((value) => value > 0 ? value + .006 : value));
  intraday[intraday.length - 1] = {
    ...intraday.at(-1)!, closePrice: null, latestPrice: intraday.at(-1)!.closePrice!, priceStage: "open",
  };
  const scores = calculateSectorLeadership(intraday, marketRows(marketReturns), dates.at(-1)!);

  assert.ok(scores.has("new-sector"));
  assert.ok((scores.get("new-sector") || 0) > 0);
});

test("최근 20거래일의 상승일이 11일 미만이면 주도력을 산출하지 않는다", () => {
  const marketReturns = Array.from({ length: 50 }, (_, index) => index < 30 ? .005 : index % 2 === 0 ? .02 : -.01);
  const scores = calculateSectorLeadership(
    sectorRows("sector", marketReturns.map((value) => value > 0 ? value + .01 : value)),
    marketRows(marketReturns), dates.at(-1)!,
  );
  assert.equal(scores.size, 0);
});

test("상승일이 많아도 시장의 20거래일 누적수익률이 음수면 산출하지 않는다", () => {
  const marketReturns = Array.from({ length: 50 }, (_, index) => index < 30 ? .005 : index < 41 ? .001 : -.02);
  const scores = calculateSectorLeadership(
    sectorRows("sector", marketReturns.map((value) => value > 0 ? value + .01 : value)),
    marketRows(marketReturns), dates.at(-1)!,
  );
  assert.equal(scores.size, 0);
});
