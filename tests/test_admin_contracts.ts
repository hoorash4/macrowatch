import test from "node:test";
import assert from "node:assert/strict";
import { validateUsername, validatePassword, validateNewSectorEtf, validateAdminCardOrder } from "../supabase/functions/admin-control/validation.ts";
import { kstTimeFromCron, updateCronTime, latestRun, githubRequest, scheduledWorkflows, updateAutomationTime, decodeBase64Utf8, deleteAutomationTime, deleteScheduledWorkflow } from "../supabase/functions/admin-control/github.ts";
import { issuerFromEtfName } from "../supabase/functions/admin-control/sector-registry.ts";

test("admin validation preserves normalization, bounds and rejection messages", () => {
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

test("KST schedule translation preserves the Korean recurrence date", () => {
  assert.equal(kstTimeFromCron("0 18 * * 6"), "03:00");
  assert.equal(updateCronTime("0 18 * * 6", "10:00"), "0 1 * * 0");
  assert.equal(updateCronTime("0 8 * * 1-5", "01:00"), "0 16 * * 0-4");
  assert.throws(() => updateCronTime("30 1 2 * *", "02:00"), /한국 날짜/);
});

test("GitHub adapter ignores historical push failures and decodes UTF-8 workflow sources", async () => {
  const original = globalThis.fetch;
  const calls: Array<string> = [];
  try {
    globalThis.fetch = async (url) => {
      calls.push(String(url));
      return new Response(JSON.stringify({workflow_runs:[
        {id:1,event:"push",conclusion:"failure",status:"completed"},
        {id:2,event:"schedule",conclusion:"success",status:"completed"},
      ]}));
    };
    const run = await latestRun("news-pipeline.yml", "test-token");
    assert.equal(run.id, 2);
    assert.equal(decodeBase64Utf8(btoa(String.fromCharCode(...new TextEncoder().encode("한국 외국인 수급")))), "한국 외국인 수급");
    assert.equal(calls[0], "https://api.github.com/repos/hoorash4/macrowatch/actions/workflows/news-pipeline.yml/runs?per_page=20");
    globalThis.fetch = async () => new Response(null, {status:204});
    assert.equal(await githubRequest("/dispatch", "test-token"), null);
    globalThis.fetch = async () => new Response(JSON.stringify({message:"denied"}), {status:403});
    await assert.rejects(githubRequest("/dispatch", "test-token"), /denied/);
  } finally { globalThis.fetch = original; }
});

test("automation schedules use card steps and are sorted by Korean time", async () => {
  const original = globalThis.fetch;
  const workflow = [
    "name: Earnings U.S. automatic",
    "on:",
    "  schedule:",
    '    - cron: "0 2 * * *"',
    '    - cron: "30 2 * * *"',
    '    - cron: "0 3 * * *"',
    "jobs: {}",
  ].join("\n");
  try {
    globalThis.fetch = async (url) => {
      const path = String(url);
      if (path.includes("/contents/.github/workflows?")) {
        return new Response(JSON.stringify([{ name: "earnings-us-automatic.yml", path: ".github/workflows/earnings-us-automatic.yml" }]));
      }
      if (path.includes("/contents/.github/workflows/earnings-us-automatic.yml")) {
        return new Response(JSON.stringify({ name: "earnings-us-automatic.yml", path: ".github/workflows/earnings-us-automatic.yml", sha: "sha", content: btoa(workflow) }));
      }
      if (path.includes("/actions/workflows?")) {
        return new Response(JSON.stringify({ workflows: [{ path: ".github/workflows/earnings-us-automatic.yml", state: "active" }] }));
      }
      if (path.includes("/runs?")) {
        return new Response(JSON.stringify({ workflow_runs: [{ id: 1, conclusion: "success", updated_at: "2026-09-10T00:00:00Z" }] }));
      }
      throw new Error(`Unexpected request: ${path}`);
    };
    const items = await scheduledWorkflows("test-token");
    assert.deepEqual(items.map((item) => [item.kst_time, item.name]), [
      ["11:00", "시총 상위 100 이익 모멘텀 · 미국 분기 실적 스냅샷"],
      ["11:30", "시총 상위 100 이익 모멘텀 · 미국 SEC 신규 공시"],
      ["12:00", "시총 상위 100 이익 모멘텀 · 미국 미확보 항목 보완"],
    ]);
  } finally { globalThis.fetch = original; }
});

test("automation step names remain stable when their cron time changes", async () => {
  const original = globalThis.fetch;
  const workflow = ["name: Sector flow", "on:", "  schedule:", '    - cron: "10 0 * * 1-5"', '    - cron: "30 3 * * 1-5"', '    - cron: "40 6 * * 1-5"', "jobs: {}"].join("\n");
  try {
    globalThis.fetch = async (url) => {
      const path = String(url);
      if (path.includes("/contents/.github/workflows?")) return new Response(JSON.stringify([{ name: "sector-flow.yml", path: ".github/workflows/sector-flow.yml" }]));
      if (path.includes("/contents/.github/workflows/sector-flow.yml")) return new Response(JSON.stringify({ name: "sector-flow.yml", path: ".github/workflows/sector-flow.yml", sha: "sha", content: btoa(workflow) }));
      if (path.includes("/actions/workflows?")) return new Response(JSON.stringify({ workflows: [{ path: ".github/workflows/sector-flow.yml", state: "active" }] }));
      if (path.includes("/runs?")) return new Response(JSON.stringify({ workflow_runs: [{ id: 1, event: "schedule", conclusion: "success", updated_at: "2026-09-10T00:00:00Z" }] }));
      throw new Error(`Unexpected request: ${path}`);
    };
    const items = await scheduledWorkflows("test-token");
    assert.deepEqual(items.map((item) => item.name), ["주도섹터 흐름 · 장초반", "주도섹터 흐름 · 장중", "주도섹터 흐름 · 종가"]);
  } finally { globalThis.fetch = original; }
});

test("schedule editing rejects workflows that can run from the resulting commit", async () => {
  const original = globalThis.fetch;
  const unsafe = [
    "name: Unsafe collector",
    "on:",
    "  push:",
    "    branches: [main]",
    "  schedule:",
    '    - cron: "0 2 * * *"',
    "jobs: {}",
  ].join("\n");
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({
      name: "unsafe.yml", path: ".github/workflows/unsafe.yml", sha: "sha", content: btoa(unsafe),
    }));
    await assert.rejects(
      updateAutomationTime("unsafe.yml", "0 2 * * *", "12:00", "test-token"),
      /다른 실행이 시작될 수 있는/,
    );
  } finally { globalThis.fetch = original; }
});

test("last schedule cannot silently become a workflow deletion", async () => {
  const original = globalThis.fetch;
  const workflow = ["name: Safe collector", "on:", "  schedule:", '    - cron: "0 2 * * *"', "jobs: {}"].join("\n");
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ name: "safe.yml", path: ".github/workflows/safe.yml", sha: "sha", content: btoa(workflow) }));
    await assert.rejects(deleteAutomationTime("safe.yml", "0 2 * * *", "test-token"), /마지막 일정/);
  } finally { globalThis.fetch = original; }
});

test("workflow deletion commits notifier update and file deletion atomically", async () => {
  const original = globalThis.fetch;
  const calls: Array<{url:string; method:string; body:string}> = [];
  const workflow = ["name: Safe collector", "on:", "  schedule:", '    - cron: "0 2 * * *"', "jobs: {}"].join("\n");
  const notifier = ["name: notifier", "on:", "  workflow_run:", "    workflows:", '      - "Safe collector"', "    types: [completed]"].join("\n");
  try {
    globalThis.fetch = async (url, init = {}) => {
      const path = String(url), method = String(init.method || "GET"), body = String(init.body || "");
      calls.push({url:path, method, body});
      if (path.includes("/contents/.github/workflows/safe.yml")) return new Response(JSON.stringify({ name: "safe.yml", path: ".github/workflows/safe.yml", sha: "safe-sha", content: btoa(workflow) }));
      if (path.includes("/contents/.github/workflows/scheduled-failure-email.yml")) return new Response(JSON.stringify({ name: "scheduled-failure-email.yml", path: ".github/workflows/scheduled-failure-email.yml", sha: "notify-sha", content: btoa(notifier) }));
      if (path.endsWith("/git/ref/heads/main")) return new Response(JSON.stringify({object:{sha:"parent"}}));
      if (path.endsWith("/git/commits/parent")) return new Response(JSON.stringify({tree:{sha:"base-tree"}}));
      if (path.endsWith("/git/trees")) return new Response(JSON.stringify({sha:"next-tree"}));
      if (path.endsWith("/git/commits")) return new Response(JSON.stringify({sha:"next-commit"}));
      if (path.endsWith("/git/refs/heads/main")) return new Response(JSON.stringify({object:{sha:"next-commit"}}));
      throw new Error(`Unexpected request: ${path}`);
    };
    await deleteScheduledWorkflow("safe.yml", "test-token");
    assert.equal(calls.filter((call) => call.url.includes("/contents/") && call.method !== "GET").length, 0);
    const tree = calls.find((call) => call.url.endsWith("/git/trees"));
    assert.match(tree!.body, /scheduled-failure-email\.yml/);
    assert.match(tree!.body, /"path":"\.github\/workflows\/safe\.yml"[^}]*"sha":null/);
    assert.equal(calls.at(-1)!.method, "PATCH");
  } finally { globalThis.fetch = original; }
});
