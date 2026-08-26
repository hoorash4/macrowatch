import test from "node:test";
import assert from "node:assert/strict";
import { scorePolicyHistory } from "../supabase/functions/_shared/policy-scoring.ts";
import type { PolicyAction, PolicyReason, PolicyScoringInput } from "../supabase/functions/_shared/policy-types.ts";

function rows(values: Array<[PolicyAction, PolicyReason, number?, boolean?]>): PolicyScoringInput[] {
  return values.map(([action, reason, change_bps = action === "hold" ? 0 : action === "hike" ? 25 : -25, is_emergency = false], index) => ({
    central_bank: "fed",
    meeting_date: `2026-${String(index + 1).padStart(2, "0")}-01`,
    action,
    ai_primary_reason: reason,
    change_bps,
    is_emergency,
  }));
}

test("물가 억제 첫 인상과 연속 결정은 첫 가중치 후 체감한다", () => {
  const result = scorePolicyHistory(rows([
    ["hike", "inflation_fight"], ["hike", "inflation_fight"], ["hike", "inflation_fight"],
  ]));
  assert.deepEqual(result.map((row) => row.final_event_score), [110, 50, 33.333]);
  assert.deepEqual(result.map((row) => row.policy_index), [1110, 1160, 1193.333]);
  assert.equal(result.at(-1)?.trend_type, "confirmed");
});

test("보험성 인하는 두 번만 체감하고 세 번째부터 보류한다", () => {
  const result = scorePolicyHistory(rows([
    ["cut", "insurance_easing"], ["cut", "insurance_easing"], ["cut", "insurance_easing", -50, true],
  ]));
  assert.deepEqual(result.map((row) => row.final_event_score), [-50, -25, 0]);
});

test("50bp와 긴급회의는 양수는 확대하고 음수는 상쇄한다", () => {
  const result = scorePolicyHistory(rows([
    ["hike", "inflation_fight", 50, true],
    ["hold", "uncertain"],
    ["hold", "uncertain"],
    ["hike", "growth_overheat", 50, true],
  ]));
  assert.equal(result[0].final_event_score, 160);
  assert.equal(result[3].final_event_score, -25);
});

test("확정 연속 추세는 첫 동결부터 100점으로 체감한다", () => {
  const result = scorePolicyHistory(rows([
    ["hike", "inflation_fight"], ["hike", "inflation_fight"], ["hike", "inflation_fight"],
    ["hold", "inflation_fight"], ["hold", "inflation_fight"], ["hold", "inflation_fight"],
  ]));
  assert.deepEqual(result.slice(3).map((row) => row.final_event_score), [-100, -50, -33.333]);
});

test("두 번의 조정은 첫 동결부터 50점으로 체감한다", () => {
  const result = scorePolicyHistory(rows([
    ["cut", "recession_financial_stress"], ["cut", "recession_financial_stress"],
    ["hold", "recession_financial_stress"], ["hold", "recession_financial_stress"],
  ]));
  assert.deepEqual(result.slice(2).map((row) => row.final_event_score), [-50, -25]);
});

test("징검다리 확정 추세는 두 번째 연속 동결부터 종료 점수를 준다", () => {
  const result = scorePolicyHistory(rows([
    ["hike", "growth_overheat"], ["hold", "growth_overheat"],
    ["hike", "growth_overheat"], ["hold", "growth_overheat"],
    ["hike", "growth_overheat"], ["hold", "growth_overheat"],
    ["hold", "growth_overheat"], ["hold", "growth_overheat"],
  ]));
  assert.deepEqual(result.slice(5).map((row) => row.final_event_score), [0, 100, 50]);
});

test("이유 변경은 방향 횟수를 유지하고 이유 횟수만 다시 시작한다", () => {
  const result = scorePolicyHistory(rows([
    ["hike", "inflation_fight"], ["hike", "inflation_fight"], ["hike", "growth_overheat"],
  ]));
  assert.equal(result[2].direction_sequence, 3);
  assert.equal(result[2].reason_sequence, 1);
  assert.equal(result[2].first_decision_adjustment, 0);
  assert.equal(result[2].final_event_score, -50);
});

test("관리자 점수는 해당 회의만 대체하고 1000 기준 지수에 누적한다", () => {
  const input = rows([["hike", "uncertain"], ["hold", "uncertain"]]);
  input[0].admin_primary_reason = "inflation_fight";
  input[0].admin_score_override = 75;
  const result = scorePolicyHistory(input);
  assert.equal(result[0].final_event_score, 75);
  assert.equal(result[0].policy_index, 1075);
  assert.equal(result[1].policy_index, 1075);
});

test("인하가 나오면 직전 상승 사이클의 최초 최고금리 회의를 고점으로 확정한다", () => {
  const input: PolicyScoringInput[] = [
    { central_bank: "fed", meeting_date: "2020-01-01", action: "hike", ai_primary_reason: "inflation_fight", target_range_upper: 5 },
    { central_bank: "fed", meeting_date: "2020-06-01", action: "hold", ai_primary_reason: "inflation_fight", target_range_upper: 5 },
    { central_bank: "fed", meeting_date: "2021-01-01", action: "cut", ai_primary_reason: "recession_financial_stress", target_range_upper: 4.75 },
  ];
  const result = scorePolicyHistory(input);
  assert.equal(result[0].is_confirmed_rate_peak, true);
  assert.equal(result[0].rate_peak_upper, 5);
  assert.equal(result[0].rate_peak_formed_date, "2020-01-01");
  assert.equal(result[1].is_confirmed_rate_peak, false);
});

test("360일 이상 된 최근 전고점에 인상으로 최초 도달하면 100점을 합산한다", () => {
  const input: PolicyScoringInput[] = [
    { central_bank: "fed", meeting_date: "2020-01-01", action: "hike", ai_primary_reason: "inflation_fight", target_range_upper: 5 },
    { central_bank: "fed", meeting_date: "2020-06-01", action: "cut", ai_primary_reason: "recession_financial_stress", target_range_upper: 4.5 },
    { central_bank: "fed", meeting_date: "2022-01-01", action: "hike", ai_primary_reason: "inflation_fight", target_range_upper: 4.75 },
    { central_bank: "fed", meeting_date: "2022-03-01", action: "hike", ai_primary_reason: "inflation_fight", target_range_upper: 5 },
    { central_bank: "fed", meeting_date: "2022-05-01", action: "hike", ai_primary_reason: "inflation_fight", target_range_upper: 5.25 },
  ];
  const result = scorePolicyHistory(input);
  assert.equal(result[3].previous_peak_reached, true);
  assert.equal(result[3].previous_peak_adjustment, 100);
  assert.equal(result[3].final_event_score, 150);
  assert.equal(result[4].previous_peak_adjustment, 0);
});

test("360일이 되기 전에 전고점에 도달하면 같은 상승 사이클에서 나중에 소급 가중하지 않는다", () => {
  const input: PolicyScoringInput[] = [
    { central_bank: "fed", meeting_date: "2020-01-01", action: "hike", ai_primary_reason: "inflation_fight", target_range_upper: 5 },
    { central_bank: "fed", meeting_date: "2020-02-01", action: "cut", ai_primary_reason: "recession_financial_stress", target_range_upper: 4.5 },
    { central_bank: "fed", meeting_date: "2020-06-01", action: "hike", ai_primary_reason: "inflation_fight", target_range_upper: 5 },
    { central_bank: "fed", meeting_date: "2021-06-01", action: "hike", ai_primary_reason: "inflation_fight", target_range_upper: 5.25 },
  ];
  const result = scorePolicyHistory(input);
  assert.equal(result[2].previous_peak_reached, true);
  assert.equal(result[2].previous_peak_adjustment, 0);
  assert.equal(result[3].previous_peak_adjustment, 0);
});

test("uncertain 동결은 추세 횟수에는 포함하지만 점수는 항상 0이다", () => {
  const result = scorePolicyHistory(rows([
    ["hike", "inflation_fight"], ["hike", "inflation_fight"], ["hike", "inflation_fight"],
    ["hold", "uncertain"], ["hold", "uncertain"],
  ]));
  assert.deepEqual(result.slice(3).map((row) => row.hold_sequence), [1, 2]);
  assert.deepEqual(result.slice(3).map((row) => row.final_event_score), [0, 0]);
});

test("징검다리 추세의 uncertain 연속 동결도 관리자 판단 없이 0이다", () => {
  const result = scorePolicyHistory(rows([
    ["hike", "growth_overheat"], ["hold", "uncertain"],
    ["hike", "growth_overheat"], ["hold", "uncertain"],
    ["hike", "growth_overheat"], ["hold", "uncertain"], ["hold", "uncertain"],
  ]));
  assert.equal(result[6].trend_type, "hold_scoring");
  assert.equal(result[6].final_event_score, 0);
});

test("정상화 인상과 인하는 모두 -50부터 이유별로 지속 체감한다", () => {
  const result = scorePolicyHistory(rows([
    ["hike", "normalization_hike"], ["hike", "normalization_hike"], ["hike", "normalization_hike"],
    ["cut", "normalization_cut"], ["cut", "normalization_cut"], ["cut", "normalization_cut"],
  ]));
  assert.deepEqual(result.map((row) => row.final_event_score), [-50, -25, -16.667, -50, -25, -16.667]);
});
