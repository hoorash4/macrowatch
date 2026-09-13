'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const chart = fs.readFileSync('assets/js/charts/economic-charts.js', 'utf8');
const css = fs.readFileSync('assets/css/economic-charts.css', 'utf8');
const html = fs.readFileSync('economic-charts.html', 'utf8');
const migration = fs.readFileSync('supabase/migrations/20260911161000_add_economic_chart_preferences.sql', 'utf8');
const catalogMigration = fs.readFileSync('supabase/migrations/20260913153000_add_economic_chart_catalog_settings.sql', 'utf8');
const categorySplitMigration = fs.readFileSync('supabase/migrations/20260913210000_split_financial_credit_category.sql', 'utf8');
const businessDistressMigration = fs.readFileSync('supabase/migrations/20260913213000_rename_business_distress_category.sql', 'utf8');

test('economic chart uses real chart-space right gap instead of a white overlay', () => {
  assert.doesNotMatch(html, /economic-plot-gap/);
  assert.doesNotMatch(css, /\.economic-plot-gap/);
  assert.doesNotMatch(chart, /economic-plot-gap|updateFixedGap/);
  assert.match(chart, /RIGHT_GAP_PX=18/);
  assert.match(chart, /if\(to>maxTo\)/);
  assert.doesNotMatch(chart, /to>=last&&to<maxTo/);
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
  assert.match(chart, /function scheduleInitialRange\(\)/);
  assert.match(chart, /scheduleInitialRange\(\);/);
  assert.doesNotMatch(chart, /pinLatestGap/);
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
  assert.match(html, /economic-charts\.css\?v=\d+/);
  assert.match(html, /economic-charts\.js\?v=\d+/);
});

test('administrator category order is stored globally and category titles are the drag handles', () => {
  assert.match(chart, /from\('economic_chart_catalog_settings'\)/);
  assert.match(chart, /from\('user_accounts'\)\.select\('is_admin'\)/);
  assert.match(chart, /if\(isAdmin\)\{title\.draggable=true/);
  assert.match(chart, /function moveCategory\(source,target\)/);
  assert.match(chart, /updated_by:user\.id/);
  assert.match(catalogMigration, /for select to authenticated[\s\S]*using \(true\)/);
  assert.match(catalogMigration, /is_admin = true/g);
  assert.match(catalogMigration, /grant insert, update .* to authenticated/);
});

test('rates and market credit use separate centralized categories without losing saved order', () => {
  assert.match(chart, /RATE_CATEGORY='금리',FINANCIAL_CREDIT_CATEGORY='금융신용'/);
  for (const code of ['HY_OAS','NFCI_CREDIT','EM_OAS']) {
    assert.match(chart, new RegExp(`code:'${code}'[^\\n]+category:FINANCIAL_CREDIT_CATEGORY`));
  }
  for (const code of ['US2Y','US10Y','US10Y_REAL','US10Y2Y','US_POLICY_RATE_MID','KR3Y','KR10Y','KR10Y3Y']) {
    assert.match(chart, new RegExp(`code:'${code}'[^\\n]+category:RATE_CATEGORY`));
  }
  assert.match(chart, /function normalizeCategoryOrder\(value\)/);
  assert.match(chart, /function normalizeSeriesOrder\(value\)/);
  assert.match(categorySplitMigration, /economic_chart_catalog_settings/);
  assert.match(categorySplitMigration, /economic_chart_preferences/);
  assert.match(categorySplitMigration, /'금융신용'/);
});

test('business distress replaces the legacy business credit category in UI and saved order', () => {
  assert.match(chart, /BUSINESS_DISTRESS_CATEGORY='기업부실'/);
  assert.match(chart, /LEGACY_BUSINESS_CREDIT_CATEGORY='기업신용'/);
  for (const code of ['US_SBDI_31_180','DRALACBS','US_SBDFI','US_COMMERCIAL_CH11','KR_CORP_DELINQ','KR_DEFAULT_COMPANIES','KR_CORP_REHAB']) {
    assert.match(chart, new RegExp(`code:'${code}'[^\\n]+category:BUSINESS_DISTRESS_CATEGORY`));
  }
  assert.match(businessDistressMigration, /economic_chart_catalog_settings/);
  assert.match(businessDistressMigration, /economic_chart_preferences/);
});

test('every paired chart defaults moving averages off from one common rule', () => {
  for (const code of ['US_CPI','US_PPI','US_PCE','KR_CPI','KR_PPI']) {
    assert.match(chart, new RegExp(`code:'${code}'[^\\n]+compareCode:`));
  }
  assert.match(chart, /compareCode:'US_CORE_CPI'/);
  assert.match(chart, /compareCode:'US_CORE_PPI'/);
  assert.match(chart, /compareCode:'US_CORE_PCE'/);
  assert.match(chart, /compareCode:'KR_CORE_CPI'/);
  assert.match(chart, /compareCode:'KR_IMPORT_PRICE'/);
  assert.match(html, /id="economic-ma-toggle"/);
  assert.match(chart, /function applyMaVisibility\(\)/);
  assert.match(chart, /!m\.compareCode&&m\.defaultMa!==false/);
  assert.match(chart, /code:'US_POLICY_RATE_MID',compareCode:'KR_POLICY_RATE'/);
  assert.match(chart, /title:'미국 PCE 가격지수'/);
});
