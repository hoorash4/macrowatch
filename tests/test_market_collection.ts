import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { stripTypeScriptTypes } from 'node:module';
import { jsonResponse } from '../supabase/functions/_shared/http.ts';

function collector(failAt = -1) {
  const calls: string[] = [];
  const delays: number[] = [];
  const source = fs.readFileSync(new URL('../supabase/functions/market-context/index.ts', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
  const context = vm.createContext({
    Deno: { serve() {} }, URLSearchParams, AbortSignal,
    setTimeout(callback: () => void, delay: number) { delays.push(delay); callback(); },
    async fetch(url: string) {
      calls.push(String(url));
      if (calls.length === failAt) return new Response(null, { status: 503 });
      const date = new URL(url).searchParams.get('basDt');
      return new Response(JSON.stringify({ response: { body: { items: { item: {
        idxNm: '코스피', basDt: date, mkp: '1,000', hipr: '1,100', lopr: '900', clpr: '1,020', trqu: '10',
      } } } } }));
    },
  });
  vm.runInContext(stripTypeScriptTypes(source) + '\nglobalThis.collect = collectCandles; globalThis.items = readItems;', context);
  return { context, calls, delays };
}

test('market collection keeps request order, key encoding and inter-request delay', async () => {
  const { context, calls, delays } = collector();
  const dates = ['2026-09-08', '2026-09-07', '2026-09-06'].map(value => new Date(value));
  const rows = await context.collect('a%2Bb%3D', dates);
  assert.deepEqual(Array.from(rows, (row: any) => row.date), dates.map(date => date.toISOString().slice(0, 10)));
  assert.deepEqual(calls.map(url => new URL(url).searchParams.get('serviceKey')), ['a+b=', 'a+b=', 'a+b=']);
  assert.deepEqual(delays, [250, 250]);
  assert.equal(rows[0].close, 1020);
  assert.equal((await context.collect('key', [])).length, 0);
});

test('market collection stops at first failed request without attempting later dates', async () => {
  const { context, calls, delays } = collector(2);
  await assert.rejects(context.collect('key', [new Date('2026-09-08'), new Date('2026-09-07'), new Date('2026-09-06')]), /503/);
  assert.equal(calls.length, 2);
  assert.deepEqual(delays, [250]);
});

test('server JSON response preserves body, status and absence of CORS headers', async () => {
  const response = jsonResponse({ error: '오류' }, 405);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('content-type'), 'application/json; charset=utf-8');
  assert.equal(response.headers.get('access-control-allow-origin'), null);
  assert.equal(await response.text(), '{"error":"오류"}');
});
