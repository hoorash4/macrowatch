import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { stripTypeScriptTypes } from 'node:module';
import { jsonResponse } from '../supabase/functions/_shared/http.ts';
import { calculateMarketContext } from '../supabase/functions/_shared/market/market-indicators.ts';

function endpoint(rows: any[], error: any = null) {
  const calls: any[] = [];
  let handler: any;
  const query = {
    select(value: string) { calls.push(['select', value]); return query; },
    eq(key: string, value: string) { calls.push(['eq', key, value]); return query; },
    order(key: string, options: any) { calls.push(['order', key, options]); return query; },
    async limit(value: number) { calls.push(['limit', value]); return { data: rows, error }; },
  };
  const source = fs.readFileSync(new URL('../supabase/functions/market-context/index.ts', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
  vm.runInNewContext(stripTypeScriptTypes(source), {
    Deno: { serve(fn: any) { handler = fn; }, env: { get: () => 'configured' } },
    json: jsonResponse, calculateMarketContext,
    createClient: () => ({ from(table: string) { calls.push(['from', table]); return query; } }),
  });
  return { handler, calls };
}
test('market context reads canonical KOSPI candles and preserves calculation', async () => {
  const rows = Array.from({ length: 100 }, (_, i) => ({
    market_date: new Date(Date.UTC(2026, 0, 100 - i)).toISOString().slice(0, 10),
    open: 1000 + i, high: 1100 + i, low: 900 + i, close: 1020 + i, volume: 10,
  }));
  const f = endpoint(rows);
  const response = await f.handler(new Request('https://example.invalid', { method: 'POST' }));
  const result = await response.json();
  assert.equal(response.status, 200);
  assert.equal(result.source, 'market_index_prices:KOSPI');
  assert.equal(result.observations, 100);
  assert.deepEqual(result.market_context, calculateMarketContext(rows.map(row => ({
    date: row.market_date, open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume,
  }))));
  assert.equal(f.calls[0][1], 'market_index_prices');
  assert.deepEqual(f.calls.find(call => call[0] === 'eq'), ['eq', 'index_code', 'KOSPI']);
  assert.deepEqual(f.calls.at(-1), ['limit', 100]);
});
test('market context rejects GET and reports missing data or database failure', async () => {
  const f = endpoint([]);
  assert.equal((await f.handler(new Request('https://example.invalid'))).status, 405);
  assert.equal(f.calls.length, 0);
  const request = () => new Request('https://example.invalid', { method: 'POST' });
  assert.equal((await f.handler(request())).status, 500);
  const failed = await endpoint([], new Error('database unavailable')).handler(request());
  assert.equal(failed.status, 500);
  assert.match((await failed.json()).error, /database unavailable/);
});
test('server JSON response preserves body, status and absence of CORS headers', async () => {
  const response = jsonResponse({ error: '오류' }, 405);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('content-type'), 'application/json; charset=utf-8');
  assert.equal(response.headers.get('access-control-allow-origin'), null);
});
