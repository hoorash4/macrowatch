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

test('economic chart fills the workspace instead of leaving unused vertical space', () => {
  assert.match(css, /\.economic-chart-panel\{[^}]*min-height:0/);
  assert.match(css, /\.economic-chart-host\{[^}]*flex:1 1 auto[^}]*height:auto/);
  assert.match(css, /@media\(max-width:850px\)[\s\S]*\.economic-chart-host\{flex:none;height:min\(470px,65vh\)/);
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

test('economic chart assets are cache-busted together', () => {
  assert.match(html, /economic-charts\.css\?v=5/);
  assert.match(html, /economic-charts\.js\?v=5/);
});
