'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const createChart = () => ({ applyOptions() {} });
const chartLibrary = {};
Object.defineProperty(chartLibrary, 'createChart', { value: createChart, writable: false });

const window = {
  MACROWATCH_CONFIG: { supabaseUrl: 'https://example.invalid', supabasePublishableKey: 'public-key' },
  LightweightCharts: chartLibrary,
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  addEventListener() {},
  dispatchEvent() {},
  localStorage: { getItem() { return null; }, setItem() {} },
};
const warnings = [];
const context = {
  window,
  console: { warn(...args) { warnings.push(args); } },
  fetch: async () => { throw new Error('not called'); },
};

vm.runInNewContext(fs.readFileSync('assets/js/core/frontend-core.js', 'utf8'), context);

assert.equal(typeof window.MacroWatchFrontend?.createFunctionClient, 'function');
assert.equal(typeof window.MacroWatchTheme?.savePreference, 'function');
assert.equal(chartLibrary.createChart, createChart);
assert.equal(warnings.length, 1);

console.log('shared frontend APIs survive a browser-specific chart adapter failure: ok');
