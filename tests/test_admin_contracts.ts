import test from "node:test";
import assert from "node:assert/strict";
import { validateTimes, validateUsername, validatePassword, validateNewSectorEtf, validateAdminCardOrder } from "../supabase/functions/admin-control/validation.ts";
import { kstTimeToCron, latestRun, githubRequest } from "../supabase/functions/admin-control/github.ts";
import { issuerFromEtfName } from "../supabase/functions/admin-control/sector-registry.ts";

test("admin validation preserves normalization, bounds and rejection messages", () => {
  assert.deepEqual(validateTimes(["20:30", "07:30"]), ["07:30", "20:30"]);
  assert.throws(() => validateTimes(["07:30", "07:30"]), /서로 다른 시간/);
  assert.throws(() => validateTimes(["24:00"]), /시간 형식/);
  assert.equal(validateUsername(" Test.ID "), "test.id");
  assert.throws(() => validateUsername("bad"), /4~32자/);
  assert.equal(validatePassword(" secret "), " secret ");
  assert.throws(() => validatePassword("short"), /6~72자/);
  assert.deepEqual(validateNewSectorEtf({ sector_name: " 반도체 ", etf_ticker: "abc123" }), {sector_name: "반도체", etf_ticker: "ABC123"});
  assert.throws(() => validateAdminCardOrder(["member-management", "member-management"]), /알 수 없는 항목/);
  assert.deepEqual(validateAdminCardOrder([]), []);
  assert.equal(issuerFromEtfName("KODEX 반도체"), "삼성자산운용");
  assert.throws(() => issuerFromEtfName("UNKNOWN ETF"), /자동 확인하지 못했습니다/);
});

test("KST schedule translation keeps midnight boundary behavior", () => {
  assert.equal(kstTimeToCron("07:30"), "30 22 * * *");
  assert.equal(kstTimeToCron("19:30"), "30 10 * * *");
});

test("GitHub adapter retains skipped-run filtering and error status behavior", async () => {
  const original = globalThis.fetch;
  const calls: Array<string> = [];
  try {
    globalThis.fetch = async (url) => {
      calls.push(String(url));
      return new Response(JSON.stringify({workflow_runs:[{id:1,conclusion:"skipped"},{id:2,conclusion:"success",status:"completed"}]}));
    };
    const run = await latestRun("news-pipeline.yml", "test-token");
    assert.equal(run.id, 2);
    assert.equal(calls[0], "https://api.github.com/repos/hoorash4/macrowatch/actions/workflows/news-pipeline.yml/runs?per_page=20");
    globalThis.fetch = async () => new Response(null, {status:204});
    assert.equal(await githubRequest("/dispatch", "test-token"), null);
    globalThis.fetch = async () => new Response(JSON.stringify({message:"denied"}), {status:403});
    await assert.rejects(githubRequest("/dispatch", "test-token"), /denied/);
  } finally { globalThis.fetch = original; }
});
