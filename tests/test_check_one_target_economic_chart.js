const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../supabase/functions/check-one-target/index.ts'), 'utf8');

test('manual economic-chart checks use the stored chart series instead of an external source', () => {
  assert.match(source, /target\.source_type === "economic_chart"/);
  assert.match(source, /const seriesCode = String\(config\.series_code \|\| ""\)\.trim\(\)/);
  assert.match(source, /\.from\("economic_chart_series_points"\)/);
  assert.match(source, /\.eq\("series_code", seriesCode\)/);
  assert.match(source, /\.order\("observation_date", \{ ascending: false \}\)/);
  assert.match(source, /const value = await collect\(target, db\)/);
});
