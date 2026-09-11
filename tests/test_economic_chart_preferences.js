'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const chart = fs.readFileSync('assets/js/charts/economic-charts.js', 'utf8');
const css = fs.readFileSync('assets/css/economic-charts.css', 'utf8');
const html = fs.readFileSync('economic-charts.html', 'utf8');
const migration = fs.readFileSync('supabase/migrations/20260911161000_add_economic_chart_preferences.sql', 'utf8');

test('economic chart uses real chart-space right gap instead of a white overlay', () => {
  assert.doesNotMatch(html, /economic-plot-gap/);
  assert.doesNotMatch(css, /\.economic-plot-gap/);
  assert.doesNotMatch(chart, /economic-plot-gap|updateFixedGap/);
  assert.match(chart, /RIGHT_GAP_PX=18/);
  assert.match(chart, /to>=last&&to<maxTo/);
});

test('economic chart frame is linked and stays at ninety percent of the prior responsive height', () => {
  assert.match(css, /\.economic-workspace\{[^}]*height:min\(64\.8vw,calc\(90dvh - 162px\)\)[^}]*min-height:324px/);
  assert.match(css, /\.economic-series-panel\{[^}]*height:100%/);
  assert.match(css, /\.economic-chart-panel\{[^}]*height:100%/);
  assert.match(css, /\.economic-chart-host\{[^}]*flex:1 1 auto/);
  assert.match(css, /@media\(max-width:850px\)[\s\S]*\.economic-chart-host\{flex:none;height:min\(423px,58\.5dvh\);min-height:270px/);
});

test('economic chart starts with exactly the latest 300 observations and labels months once', () => {
  assert.match(chart, /DEFAULT_VISIBLE_BARS=300/);
  assert.match(chart, /const count=Math\.min\(DEFAULT_VISIBLE_BARS,rows\.length\),last=rows\.length-1/);
  assert.match(chart, /from:last-count\+1,to:last\+rightGapBars\(\)/);
  assert.match(chart, /initialRangePending=true;showInitialRange\(\)/);
  assert.match(chart, /if\(initialRangePending\)\{initialRangePending=false;showInitialRange\(\);\}/);
  assert.match(chart, /rebuildMonthTickDates\(data\)/);
  assert.match(chart, /if\(!monthTickDates\.has\(timeKey\(time\)\)\)return''/);
});

test('economic chart personal state is persisted per authenticated user', () => {
  assert.doesNotMatch(chart, /localStorage/);
  assert.match(chart, /from\('economic_chart_preferences'\)/);
  assert.match(chart, /series_order/);
  assert.match(chart, /hidden_series/);
  assert.match(chart, /horizontal_lines/);
  assert.match(chart, /restorePlainLines\(\)/);
  assert.match(chart, /savePlainLinesFromChart\(\)/);

  assert.match(migration, /user_id uuid primary key references auth\.users\(id\) on delete cascade/);
  assert.match(migration, /enable row level security/);
  assert.match(migration, /auth\.uid\(\) = user_id/g);
  assert.match(migration, /grant select, insert, update, delete .* to authenticated/);
});

test('economic chart assets are cache-busted for the changed script', () => {
  assert.match(html, /economic-charts\.css\?v=7/);
  assert.match(html, /economic-charts\.js\?v=8/);
});