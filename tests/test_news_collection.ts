import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { stripTypeScriptTypes } from 'node:module';
import { webcrypto } from 'node:crypto';

test('all configured news feeds are collected and individual failures preserve other sources', async () => {
  const calls: string[] = [];
  const source = fs.readFileSync(new URL('../supabase/functions/news-pipeline/index.ts', import.meta.url), 'utf8')
    .replace(/^import .*;\r?\n/gm, '');
  const context = vm.createContext({
    Deno: { serve() {} }, AbortSignal, TextEncoder, crypto: webcrypto,
    async fetch(url: string) {
      calls.push(url);
      if (url.endsWith('economy.xml')) return new Response(null, { status: 503 });
      return new Response(`<rss><item><title>${url}</title><description>summary ${url}</description><link>${url}</link><pubDate>${new Date().toUTCString()}</pubDate></item></rss>`);
    },
  });
  vm.runInContext(stripTypeScriptTypes(source) + '\nglobalThis.collect = collectCandidates;', context);
  const result = await context.collect(24);
  assert.equal(calls.length, 8);
  assert.deepEqual([...new Set(result.candidates.map((item: any) => item.source))].sort(),
    ['financial_news', 'maekyung', 'yonhap']);
  assert.equal(result.candidates.length, 6);
  assert.equal(result.errors.length, 2);
  assert.deepEqual(Array.from(result.errors, (item: any) => item.source), ['yonhap', 'financial_news']);
  assert.ok(result.errors.every((item: any) => item.error.includes('503')));
});
