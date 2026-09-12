const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const ROOT = path.resolve(__dirname, '..');
const chart = fs.readFileSync(path.join(ROOT, 'assets/js/charts/economic-charts.js'), 'utf8');
const manager = fs.readFileSync(path.join(ROOT, 'assets/js/charts/economic-series-manager.js'), 'utf8');
const automatic = fs.readFileSync(path.join(ROOT, 'backend/signals/economic_chart_automatic.py'), 'utf8');
const html = fs.readFileSync(path.join(ROOT, 'economic-charts.html'), 'utf8');

function codesFrom(source, marker) {
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${marker} missing`);
  const tail = source.slice(start);
  const end = tail.indexOf('];');
  assert.notEqual(end, -1, `${marker} terminator missing`);
  return [...tail.slice(0, end).matchAll(/code\s*:\s*'([^']+)'/g)].map(match => match[1]);
}

test('economic chart list removal only changes per-user visibility, never collection scope', () => {
  assert.match(chart, /function hideSeries\(m\).*hidden\.add\(m\.code\).*saveHidden\(hidden\)/s);
  assert.doesNotMatch(chart, /hideSeries\(m\).*economic_chart_points.*delete/s);
  assert.doesNotMatch(automatic, /economic_chart_preferences|hidden_series/);
  assert.match(automatic, /Collect every regular economic-chart series in one scheduled refresh/);
});

test('existing series add UI offers only hidden series and removes selected codes from hidden preferences', () => {
  assert.match(manager, /\+ 기존 지표 추가/);
  assert.match(manager, /if \(!hidden\.has\(item\.code\)\) continue/);
  assert.match(manager, /filter\(code => !selected\.has\(code\)\)/);
  assert.match(manager, /economic_chart_preferences/);
  assert.match(manager, /location\.reload\(\)/);
  assert.match(html, /economic-series-manager\.js\?v=\d+/);
  assert.match(html, /economic-series-manager\.css\?v=\d+/);
});

test('series manager catalog cannot silently drift from the economic chart catalog', () => {
  const chartCodes = codesFrom(chart, 'const SERIES=[').sort();
  const managerCodes = codesFrom(manager, 'const SERIES_CATALOG = [').sort();
  assert.deepEqual(managerCodes, chartCodes);
});
