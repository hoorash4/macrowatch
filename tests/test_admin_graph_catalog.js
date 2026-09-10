const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

global.window = {};
global.document = { readyState: 'loading', addEventListener() {} };
require('../assets/js/admin/admin-graph-catalog.js');

const catalog = global.window.MacroWatchAdminGraphCatalog;

test('graph catalog covers every dashboard menu and chart card', () => {
  assert.deepEqual(catalog.MENUS.map((menu) => menu.id), ['overview', 'policy', 'earnings', 'stress']);
  assert.equal(catalog.MENUS.reduce((sum, menu) => sum + menu.charts.length, 0), 16);
  assert.ok(catalog.MENUS.every((menu) => menu.charts.every((chart) => chart.series.length && chart.components.length)));
});

test('graph catalog renders menu groups collapsed by default', () => {
  const container = { innerHTML: '' };
  const summary = { textContent: '' };
  const fakeDocument = { getElementById: (id) => id === 'graph-component-catalog' ? container : id === 'graph-catalog-summary' ? summary : null };
  assert.equal(catalog.render(fakeDocument), true);
  assert.equal((container.innerHTML.match(/<details /g) || []).length, 4);
  assert.doesNotMatch(container.innerHTML, /<details[^>]*\sopen(?:\s|>)/);
  assert.equal(summary.textContent, '4개 메뉴 · 16개 그래프');
});

test('admin separates operational controls from the information catalog', () => {
  const root = path.resolve(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'admin.html'), 'utf8');
  const script = fs.readFileSync(path.join(root, 'assets/js/admin/admin.js'), 'utf8');
  assert.match(html, /data-admin-tab="management"/);
  assert.match(html, /data-admin-tab="information"/);
  assert.match(html, /id="admin-management-panel"[^>]*data-admin-tab-panel="management"/);
  assert.match(html, /id="admin-information-panel"[^>]*data-admin-tab-panel="information"[^>]*class="hidden"/);
  assert.match(script, /container: document\.getElementById\('admin-management-panel'\)/);
  assert.match(script, /anchor: document\.getElementById\('admin-management-anchor'\)/);
});
