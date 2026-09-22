import test from "node:test";
import assert from "node:assert/strict";

(globalThis as any).Deno = { env: { get: () => undefined } };
const { alertableAiModels, availableAiModels, configuredAiModel, selectableAiModels } = await import("../supabase/functions/_shared/policy/ai-model-selection.ts");

test("model choices keep the current tier and only later versions", () => {
  const models = [
    { id: "gpt-6-sol" }, { id: "gpt-6-luna" }, { id: "gpt-6.1-sol" },
    { id: "gpt-7-sol" }, { id: "gpt-7-sol-2026-09-23" }, { id: "gpt-5.6-sol" },
    { id: "gpt-8-sol", shutdown_date: "2020-01-01" },
  ];
  assert.deepEqual(selectableAiModels(models, "gpt-6-sol", "fomc"), [
    { id: "gpt-6-sol", current: true },
    { id: "gpt-7-sol", current: false },
    { id: "gpt-6.1-sol", current: false },
  ]);
  assert.deepEqual(selectableAiModels(models, "gpt-6-luna", "standard"), [
    { id: "gpt-6-luna", current: true },
  ]);
});

test("configured models keep deployed defaults until an admin selection is saved", async () => {
  const client = (value: unknown) => ({
    from: () => ({ select: () => ({ eq: () => ({ maybeSingle: async () => ({ data: value == null ? null : { value }, error: null }) }) }) }),
  });
  assert.equal(await configuredAiModel(client(null), "fomc"), "gpt-6-sol");
  assert.equal(await configuredAiModel(client(null), "standard"), "gpt-6-luna");
  assert.equal(await configuredAiModel(client({ fomc: "gpt-7-sol", standard: "gpt-7-luna" }), "standard"), "gpt-7-luna");
  await assert.rejects(configuredAiModel(client({ fomc: "gpt-7-luna" }), "fomc"), /올바르지 않습니다/);
});

test("model list errors never masquerade as no newer model", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ data: [{ id: "gpt-7-sol" }] }));
    assert.deepEqual(await availableAiModels("test-key"), [{ id: "gpt-7-sol" }]);
    globalThis.fetch = async () => new Response("denied", { status: 403 });
    await assert.rejects(availableAiModels("test-key"), /403/);
  } finally { globalThis.fetch = original; }
});

test("monthly notice finds newer GPT versions without depending on tier names", () => {
  const models = [
    { id: "gpt-5.6-sol" }, { id: "gpt-6-astra" }, { id: "gpt-7-terra" },
    { id: "gpt-7-nova" }, { id: "gpt-7.1" }, { id: "gpt-image-7" },
    { id: "gpt-8-nova", shutdown_date: "2020-01-01" },
  ];
  assert.deepEqual(alertableAiModels(models, { fomc: "gpt-6-sol", standard: "gpt-6-luna" }, []), [
    "gpt-7.1", "gpt-7-nova", "gpt-7-terra",
  ]);
  assert.deepEqual(alertableAiModels(models, { fomc: "gpt-6-sol", standard: "gpt-6-luna" }, ["gpt-7-nova"]), [
    "gpt-7.1", "gpt-7-terra",
  ]);
  assert.deepEqual(alertableAiModels(models, { fomc: "gpt-7-terra", standard: "gpt-6-luna" }, []), [
    "gpt-7.1", "gpt-7-nova",
  ]);
});
