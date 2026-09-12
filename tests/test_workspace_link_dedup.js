'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('assets/js/core/frontend-core.js', 'utf8');

async function run(existingHref = null) {
  const inserted = [];
  const ready = [];
  const actions = { before(link) { inserted.push(link); } };
  const existing = existingHref ? [{ getAttribute() { return existingHref; } }] : [];
  const document = {
    baseURI: 'https://example.invalid/economic-charts.html', readyState: 'loading',
    documentElement: { dataset: {} }, head: { append() {} },
    createElement() { return { dataset: {}, addEventListener() {}, querySelector() { return null; } }; },
    getElementById() { return null; },
    querySelector(selector) { return selector === '.dashboard-nav-actions' ? actions : null; },
    querySelectorAll(selector) { return selector === 'a[href]' ? existing : []; },
    addEventListener(type, handler) { if (type === 'DOMContentLoaded') ready.push(handler); },
  };
  const window = {
    MACROWATCH_CONFIG: {
      supabaseUrl: 'https://project.invalid', supabasePublishableKey: 'public',
      workspaceLinks: [{ href: 'economic-charts.html', label: '경제지표 차트', target: '_self' }],
    },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    localStorage: { getItem() { return null; }, setItem() {} },
    addEventListener() {}, dispatchEvent() {}, supabase: null,
  };
  vm.runInNewContext(source, { window, document, URL, Array, console, CustomEvent: class {}, queueMicrotask });
  await Promise.all(ready.map(handler => handler()));
  return inserted;
}

(async () => {
  assert.equal((await run('economic-charts.html')).length, 0, 'existing chart navigation link must not be duplicated');
  assert.equal((await run()).length, 1, 'dashboard without the link must receive the configured workspace link');
  console.log('configured workspace link is inserted once per page destination: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
