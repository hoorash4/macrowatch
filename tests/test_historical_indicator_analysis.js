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
  assert.equal(api.FREQUENCY_RULES.M.window,2);
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

test('selection removes filtered indicators and promotes the first remaining checked indicator',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-selection.js','MacroWatchHistoricalIndicatorSelection'),state=api.create(),items=['A','B','C'].map(code=>({meta:{code}}));
  assert.equal(state.reconcile(items).active,'A');state.toggle('B',true);state.activate('B');state.toggle('B',false);assert.equal(state.snapshot().active,'A');state.reconcile(items.slice(1));assert.equal(state.snapshot().active,'B');
});

test('coverage view is canonical, security-invoker, and read-only for authenticated users',()=>{
  const sql=read('supabase/migrations/20260915144500_add_economic_series_coverage.sql');
  assert.match(sql,/economic_chart_series_coverage\s*\nwith \(security_invoker = true\)/);
  assert.match(sql,/from public\.economic_chart_series_points/);
  assert.match(sql,/min\(observation_date\) as first_date/);
  assert.match(sql,/revoke all[^;]*authenticated/);
  assert.match(sql,/grant select[^;]*authenticated, service_role/);
});

test('historical indicator UI exposes one accordion, one global filter, dual axes, and current signal',()=>{
  const html=read('historical-insight.html'),chart=read('assets/js/historical-insight/historical-index-chart.js'),controller=read('assets/js/historical-insight/historical-insight.js');
  assert.equal((html.match(/id="historical-indicator-accordion"/g)||[]).length,1);
  assert.match(html,/data-indicator-strength="strong"[\s\S]*data-indicator-strength="standard"[\s\S]*data-indicator-strength="weak"/);
  assert.match(chart,/priceScaleId:'left'/);assert.match(chart,/leftPriceScale: \{ visible: true/);
  assert.match(controller,/새로운 사이클 시작 가능성|historical-cycle-signal/);
  assert.match(controller,/과거에 선행·동행했던 전체 지표를 감시합니다/);
});
