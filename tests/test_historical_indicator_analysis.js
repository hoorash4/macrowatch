const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const test=require('node:test');
const vm=require('node:vm');
const ROOT=path.resolve(__dirname,'..');
const read=file=>fs.readFileSync(path.join(ROOT,file),'utf8');
function load(file,name){const window={};vm.runInNewContext(read(file),{window});return window[name];}

test('indicator thresholds and frequency pivot rules are centralized',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-analysis.js','MacroWatchHistoricalIndicatorAnalysis');
  assert.deepEqual({...api.FILTER_THRESHOLDS},{strong:80,standard:65,weak:40});
  assert.equal(api.CURRENT_SIGNAL_POLICY.activeThreshold,65);
  assert.equal(api.CURRENT_SIGNAL_POLICY.invalidThreshold,40);
  assert.equal(api.FREQUENCY_RULES.M.window,2);
});

test('current pivot probability rises with active pivots and falls with invalidated pivots',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-analysis.js','MacroWatchHistoricalIndicatorAnalysis');
  const result={leadDays:10,trendConsistency:90},item=(status,code)=>({meta:{code,frequency:'D'},pastConsistency:90,evidence:{status,result}});
  const one=api.currentPivotProbability([item('active','A')]);
  const two=api.currentPivotProbability([item('active','A'),item('active','B')]);
  const weakened=api.currentPivotProbability([item('active','A'),item('invalidated','B')]);
  assert.ok(two.probability>one.probability);
  assert.ok(weakened.probability<two.probability);
  assert.deepEqual({active:weakened.activeCount,invalidated:weakened.invalidatedCount},{active:1,invalidated:1});
});

test('pivot analysis uses raw values and reports a prior local low as a rising lead',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-analysis.js','MacroWatchHistoricalIndicatorAnalysis');
  const values=[8,6,2,3,4,5,6,7,8,9,10,11];
  const rows=values.map((value,index)=>({time:`2000-${String(index+1).padStart(2,'0')}-01`,value}));
  const meta={code:'X',title:'X',frequency:'M'},item={searchStart:'2000-01-01',searchEnd:'2000-12-01'},cycle={startDate:'2000-05-01',peakDate:'2000-12-01',troughDate:null};
  const result=api.analyzeHistorical(meta,rows,item,cycle).results.find(row=>row.referenceType==='START');
  assert.equal(result.pivotDate,'2000-03-01');
  assert.equal(result.pivotValue,2);
  assert.equal(result.timingType,'leading');
  assert.equal(result.trendDirection,'rising');
  assert.ok(result.trendConsistency>90);
});

test('display normalization stays separate from raw values and does not invert direction',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-analysis.js','MacroWatchHistoricalIndicatorAnalysis');
  const normalized=api.normalizeForDisplay([{time:'2020-01-01',value:10},{time:'2020-02-01',value:20},{time:'2020-03-01',value:30}],'2020-01-01','2020-03-01');
  assert.deepEqual(Array.from(normalized,row=>Math.round(row.value)),[0,50,100]);
  assert.deepEqual(Array.from(normalized,row=>row.rawValue),[10,20,30]);
});

test('selection starts with only the index chart and retains explicit indicator choices',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-selection.js','MacroWatchHistoricalIndicatorSelection'),state=api.create(),items=['A','B','C'].map(code=>({meta:{code}}));
  assert.equal(state.reconcile(items).active,null);assert.equal(state.snapshot().checked.length,0);state.toggle('B',true);state.activate('B');assert.equal(state.snapshot().active,'B');state.reconcile(items.slice(1));assert.equal(state.snapshot().active,'B');
});

test('indicator data spans twenty-four months around the cycle without changing the chart viewport',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-analysis.js','MacroWatchHistoricalIndicatorAnalysis'),item={searchStart:'2018-01-01'};
  assert.deepEqual({...api.displayWindow(item,{startDate:'2020-03-31',troughDate:'2022-12-31'},'2026-09-15')},{from:'2018-03-31',to:'2024-12-31'});
  const controller=read('assets/js/historical-insight/historical-insight.js');
  assert.doesNotMatch(controller,/checked\.length\)chart\?\.focus|context\.displayRange\.from,context\.displayRange\.to,0/);
});

test('US indices exclude Korea-only indicators while KOSPI keeps both countries',()=>{
  const ctx={window:{MacroWatchEconomicSeriesRegistry:{allSeries:[{code:'US2Y'},{code:'KR3Y'},{code:'KR_POLICY_RATE'},{code:'KOSPI_PBR'},{code:'USDKRW'}]}}};
  vm.runInNewContext(read('assets/js/historical-insight/historical-indicator-data.js'),ctx);
  const repo=ctx.window.MacroWatchHistoricalIndicators.createRepository({});
  assert.deepEqual(Array.from(repo.catalog('SP500'),item=>item.code),['US2Y']);
  assert.deepEqual(Array.from(repo.catalog('KOSPI'),item=>item.code),['US2Y','KR3Y','KR_POLICY_RATE','KOSPI_PBR','USDKRW']);
});

test('coverage view is canonical, security-invoker, and read-only for authenticated users',()=>{
  const sql=read('supabase/migrations/20260915144500_add_economic_series_coverage.sql');
  assert.match(sql,/economic_chart_series_coverage\s*\nwith \(security_invoker = true\)/);
  assert.match(sql,/from public\.economic_chart_series_points/);
  assert.match(sql,/min\(observation_date\) as first_date/);
  assert.match(sql,/revoke all[^;]*authenticated/);
  assert.match(sql,/grant select[^;]*authenticated, service_role/);
});

test('historical indicator UI hides the indicator axis and uses dashed pivot guides',()=>{
  const html=read('historical-insight.html'),chart=read('assets/js/historical-insight/historical-index-chart.js'),controller=read('assets/js/historical-insight/historical-insight.js');
  assert.equal((html.match(/id="historical-indicator-accordion"/g)||[]).length,1);
  assert.match(html,/data-indicator-strength="strong"[\s\S]*data-indicator-strength="standard"[\s\S]*data-indicator-strength="weak"/);
  assert.match(chart,/priceScaleId:'left'/);assert.match(chart,/leftPriceScale: \{ visible: false/);
  assert.match(chart,/crosshairMarkerVisible:false/);assert.doesNotMatch(chart,/line\.setMarkers/);
  assert.match(chart,/historical-indicator-pivot-line/);assert.match(chart,/timeToCoordinate/);
  assert.match(controller,/displayWindow/);assert.match(controller,/normalizeForDisplay\(item\.rows,context\.displayRange\.from,context\.displayRange\.to\)/);
  assert.match(controller,/주가 피봇 가능성|historical-cycle-signal/);
  assert.match(controller,/과거에 한 번이라도 유효했던 선행·동행 지표 전체를 매일 감시합니다/);
  assert.match(controller,/historical-filter'\)\.hidden=currentMode/);
});

test('current regime title setting is authenticated read and administrator update only',()=>{
  const sql=read('supabase/migrations/20260915151000_add_historical_current_settings.sql');
  assert.match(sql,/current_name text not null default '현재 국면 관찰 중'/);
  assert.match(sql,/Authenticated users read historical current settings/);
  assert.match(sql,/Administrators update historical current settings/);
  assert.match(sql,/revoke all[^;]*anon, authenticated/);
  assert.match(sql,/grant select, update[^;]*authenticated/);
  assert.doesNotMatch(sql,/grant insert[^;]*authenticated/);
});
