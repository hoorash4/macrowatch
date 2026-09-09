const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/js/charts/analysis-chart-utils.js'), 'utf8'), context);
const domain = context.window.MacroWatchAnalysisChart.visibleAxisDomain;
const axisDomain = context.window.MacroWatchAnalysisChart.axisDomain;
const source = fs.readFileSync(path.join(__dirname, '../assets/js/charts/analysis-chart-utils.js'), 'utf8');
const styles = fs.readFileSync(path.join(__dirname, '../assets/css/styles.css'), 'utf8');

test('chart profiles centralize the two-year default and structural options', () => {
  const utils = context.window.MacroWatchAnalysisChart;
  assert.equal(utils.DEFAULT_RANGE_YEARS, 2);
  assert.equal(utils.chartProfiles.main.defaultYears, 2);
  assert.equal(utils.chartProfiles.main.axisMode, 'single');
  assert.deepEqual([...utils.chartProfiles.main.auxiliaryPanels], []);
  const profile = utils.chartProfile({ axisMode: 'dual', auxiliaryPanels: ['first', 'second'], cursorSeries: [{ key: 'main', label: '메인' }] });
  assert.equal(profile.defaultYears, 2);
  assert.equal(profile.axisMode, 'dual');
  assert.deepEqual([...profile.auxiliaryPanels], ['first', 'second']);
  assert.equal(utils.cursorValueText({ main: 12.345, comparison: 99 }, [{ key: 'main', label: '메인', format: value => value.toFixed(1) }]), '메인 12.3');
});

test('every dashboard chart declares the common profile or common default', () => {
  const modules = [
    '../assets/js/charts/policy-chart.js',
    '../assets/js/charts/policy-expectation-chart.js',
    '../assets/js/charts/em-capacity-chart.js',
    '../assets/js/charts/korea-foreign-flow-chart.js',
    '../assets/js/charts/liquidity-chart.js',
    '../assets/js/charts/equity-bond-attractiveness-chart.js',
    '../assets/js/charts/korea-earnings-chart.js',
    '../assets/js/charts/inflation-chart.js',
    '../assets/js/charts/small-business-risk-chart.js',
    '../assets/js/charts/korea-small-business-risk-chart.js',
  ];
  for (const file of modules) assert.match(fs.readFileSync(path.join(__dirname, file), 'utf8'), /chartProfile\(/, file);
  const dashboard = fs.readFileSync(path.join(__dirname, '../assets/js/dashboard/dashboard-charts.js'), 'utf8');
  assert.match(dashboard, /STRESS_RANGE_DEFAULT_YEARS = String\(DEFAULT_RANGE_YEARS\)/);
  assert.match(dashboard, /US_MSI_PROFILE = chartProfile/);
  assert.match(dashboard, /KOREA_MSI_PROFILE = chartProfile/);
  assert.match(dashboard, /EM_MSI_PROFILE = chartProfile/);
  assert.match(dashboard, /const profile = createProfile\(\{ cursorSeries:[\s\S]*series\.map/);
});

test('scrollable SVG fills the same vertical plot area as its fixed Y axis', () => {
  assert.match(source, /svg\.setAttribute\('preserveAspectRatio', 'none'\)/);
  assert.match(source, /fixedAxis\.setAttribute\('preserveAspectRatio', 'none'\)/);
  assert.match(source, /height:\$\{viewHeight\}px/);
  assert.doesNotMatch(source, /width:72px;height:100%/);
});

test('reference lines move with the visible Y-axis domain', () => {
  assert.match(source, /axis\.referenceLines\.forEach/);
  assert.match(source, /const pixel = map\(value\)\.toFixed\(2\)/);
  assert.match(source, /node\.setAttribute\('y1', pixel\)/);
  assert.match(source, /node\.setAttribute\('y2', pixel\)/);
});

test('all chart series use the centralized three-level line width tokens', () => {
  assert.match(styles, /--chart-line-primary-width: 2\.5;/);
  assert.match(styles, /--chart-line-auxiliary-width: 2;/);
  assert.match(styles, /--chart-line-comparison-width: 2;/);
  assert.match(source, /primary: 'var\(--chart-line-primary-width\)'/);
  assert.match(source, /auxiliary: 'var\(--chart-line-auxiliary-width\)'/);
  assert.match(source, /comparison: 'var\(--chart-line-comparison-width\)'/);
  assert.match(source, /stress: \{ stroke: '#00838c', width: lineWidths\.primary \}/);
  assert.match(source, /raw: \{ stroke: \['#b4535d', '#2563a8'\], width: lineWidths\.comparison/);
  const consumers = [
    '../assets/js/dashboard/dashboard-charts.js',
    '../assets/js/charts/inflation-chart.js',
    '../assets/js/charts/liquidity-chart.js',
    '../assets/js/charts/equity-bond-attractiveness-chart.js',
    '../assets/js/charts/korea-earnings-chart.js',
  ].map(file => fs.readFileSync(path.join(__dirname, file), 'utf8')).join('\n');
  assert.match(consumers, /lineWidths\.primary/);
  assert.match(consumers, /lineWidths\.auxiliary/);
  assert.match(consumers, /lineWidths\.comparison/);
  assert.doesNotMatch(consumers, /stroke-width="(?:3\.25|3|2\.75|2\.5|2\.25|2\.2|1\.75|1\.15)"/);
});

test('visible domain preserves duplicate boundary coordinates and input ordering', () => {
  const points = [{ x: 9, value: 20 }, { x: 1, value: 30 }, { x: 9, value: 50 },
    { x: 12, value: 100 }, { x: 11, value: 200 }, { x: 12, value: 300 }, { x: 10, value: null }];
  const values = [20, 50, 100, 300];
  const expected = axisDomain(values, { minimumSpan: 6 });
  assert.equal(JSON.stringify(domain(points, 10, 10)), JSON.stringify(expected));
});

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

test('common axis domain reserves ten percent at both plot edges', () => {
  const linear = axisDomain([20, 100]);
  assert.ok(Math.abs((100 - 20) / (linear.max - linear.min) - .8) < 1e-12);
  assert.ok(Math.abs((20 - linear.min) / (linear.max - linear.min) - .1) < 1e-12);
  assert.ok(Math.abs((linear.max - 100) / (linear.max - linear.min) - .1) < 1e-12);
  const zeroAxis = axisDomain([10, 40], { includeZero: true });
  assert.ok(zeroAxis.min < 0);
  const symmetric = axisDomain([-20, 100], { symmetric: true });
  assert.equal(symmetric.min, -symmetric.max);
});

