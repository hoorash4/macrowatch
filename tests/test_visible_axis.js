const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../analysis-chart-utils.js'), 'utf8'), context);
const domain = context.window.MacroWatchAnalysisChart.visibleAxisDomain;

test('visible window excludes distant spikes but includes boundary samples', () => {
  const points = [{ x: 0, value: 1000 }, { x: 10, value: 2 }, { x: 20, value: 3 }, { x: 30, value: 4 }, { x: 40, value: 5 }];
  const recent = domain(points, 20, 40);
  assert.ok(recent.max < 10);
  assert.ok(recent.min < 2 && recent.max > 5);
  assert.ok(domain(points, 0, 20).max > 1000);
});
test('auxiliary signals keep a symmetric zero-centred range including negatives', () => {
  const result = domain([{ x: 0, value: -3 }, { x: 1, value: 1 }], 0, 1, true);
  assert.equal(result.min, -result.max);
  assert.ok(result.min < -3);
});
test('missing values are not zero and flat data retains a finite span', () => {
  const result = domain([{ x: 0, value: null }, { x: 1, value: 20 }, { x: 2, value: 20 }], 0, 2);
  assert.ok(result.min > 19 && result.max > 20);
  assert.equal(domain([{ x: 0, value: null }], 0, 1), null);
});

