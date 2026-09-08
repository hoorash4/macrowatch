const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

// Exercise the production renderers without authentication or database writes.
function render(kind, rows) {
  const host = { innerHTML: '', querySelector: () => null };
  const context = {
    window: { MacroWatchFrontend: { escapeHtml: String, createSupabaseClient: () => null } },
    document: { getElementById: () => host, querySelector: () => null, querySelectorAll: () => [] },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/js/charts/analysis-chart-utils.js'), 'utf8'), context);
  context.window.MacroWatchAnalysisChart.scrollableSvg = () => {};
  const source = fs.readFileSync(path.join(__dirname, '../assets/js/dashboard/dashboard-charts.js'), 'utf8');
  vm.runInContext(source.replace('window.MacroWatchChartUtils =', 'window.testRenderers = { weekly: renderMarketStressAndTensionChart, monthly: renderMarketStressDashboard }; window.MacroWatchChartUtils ='), context);
  context.window.testRenderers[kind](rows);
  const d = host.innerHTML.match(/<path d="([^"]*)"[^>]*stroke="#6b7280"/)[1];
  const numbers = d.match(/-?\d+(?:\.\d+)?/g)?.map(Number) || [];
  return { html: host.innerHTML, d, end: numbers.slice(-2) };
}

const rows = [
  { week: '2026-08-21', tension_index: '10.82', sp500_friday_close: '7674.37' },
  { week: '2026-08-28', tension_index: '10.73', sp500_friday_close: '7711.76' },
  { week: '2026-09-04', tension_index: '10.81', sp500_friday_close: '7718.60', is_provisional: true },
  { week: '2026-09-11', tension_index: '10.81', sp500_friday_close: null, is_provisional: true },
];

test('주간 S&P 500 미발표 종가는 선과 축에서 0으로 바뀌지 않는다', () => {
  const actual = render('weekly', rows);
  const complete = render('weekly', rows.map(row => ({ ...row, sp500_friday_close: row.sp500_friday_close ?? '7718.60' })));
  assert.ok(actual.end[0] < complete.end[0], 'missing latest price must not extend the line');
  assert.equal(actual.end[1], complete.end[1], 'missing price must not change the price scale');
  assert.ok(actual.end[1] >= 20 && actual.end[1] <= 343);
  const ticks = [...actual.html.matchAll(/<text[^>]*fill="#6b7280"[^>]*>([^<]+)<\/text>/g)].map(match => Number(match[1].replaceAll(',', '')));
  assert.ok(ticks.length && Math.min(...ticks) > 6000);
});

test('주간 S&P 500 자료가 전부 없으면 선을 그리지 않는다', () => {
  assert.equal(render('weekly', rows.map(row => ({ ...row, sp500_friday_close: null }))).d, '');
});

test('중간 미발표 종가를 가로질러 선을 연결하지 않는다', () => {
  const gap = [
    ...rows.slice(0, 2),
    { ...rows[2], sp500_friday_close: null },
    { ...rows[3], sp500_friday_close: '7720' },
    { week: '2026-09-18', tension_index: '10.8', sp500_friday_close: '7730' },
  ];
  assert.equal((render('weekly', gap).d.match(/M /g) || []).length, 2);
});

test('월간 대체 그래프에서도 미발표 종가는 선과 점에서 제외한다', () => {
  const monthly = rows.map((row, index) => ({ month: `2026-0${index + 5}-01`, stress_index: row.tension_index, sp500_month_end_close: row.sp500_friday_close }));
  const actual = render('monthly', monthly);
  const complete = render('monthly', monthly.map(row => ({ ...row, sp500_month_end_close: row.sp500_month_end_close ?? '7718.60' })));
  assert.ok(actual.end[0] < complete.end[0]);
  assert.equal(actual.end[1], complete.end[1]);
  assert.equal((actual.html.match(/<circle[^>]*fill="#6b7280"/g) || []).length, 3);
});
