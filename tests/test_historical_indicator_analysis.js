const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const test=require('node:test');
const vm=require('node:vm');
const ROOT=path.resolve(__dirname,'..');
const read=file=>fs.readFileSync(path.join(ROOT,file),'utf8');
function load(file,name,window={}){vm.runInNewContext(read(file),{window});return window[name];}
const analysis=()=>load('assets/js/historical-insight/historical-indicator-analysis.js','MacroWatchHistoricalIndicatorAnalysis');
const month=(index,value)=>{const d=new Date(Date.UTC(2000,index,1));return{time:d.toISOString().slice(0,10),value};};
const segments=(defs)=>{let n=0,value=100,out=[];for(const [months,delta] of defs){for(let i=0;i<months;i++){out.push(month(n++,value));value+=delta;}}return out;};

test('regime and scoring policies are centralized',()=>{
  const a=analysis();assert.equal(a.ANALYSIS_POLICY.minimumRegimeDays,90);assert.equal(a.ANALYSIS_POLICY.extremeValidityDays,90);
  assert.equal(a.ANALYSIS_POLICY.relevanceBeforeDays,183);assert.equal(a.ANALYSIS_POLICY.relevanceAfterDays,31);
  assert.deepEqual({...a.ANALYSIS_POLICY.referenceWeights},{timing:.6,duration:.4});
});

test('short correction does not become a regime, while four-month flat and falling sections do',()=>{
  const a=analysis(),noise=segments([[5,3],[1,-1],[4,3]]),flat=segments([[6,3],[4,0]]),fall=segments([[5,0],[4,-3]]);
  assert.equal(a.detectRegimes(noise).filter(x=>x.type==='falling').length,0);
  assert.ok(a.detectRegimes(flat).some(x=>x.type==='sideways'));
  assert.ok(a.detectRegimes(fall).some(x=>x.type==='falling'));
});

test('falling into three-month sideways and long cycles retain intermediate boundaries',()=>{
  const a=analysis(),fallFlat=segments([[7,-3],[4,0]]),long=segments([[6,3],[4,0],[7,3]]);
  assert.ok(a.detectPivots(fallFlat).some(x=>x.previousRegime==='falling'&&x.nextRegime==='sideways'));
  assert.ok(a.detectRegimes(long).some(x=>x.type==='sideways'));
  assert.ok(a.detectPivots(long).length>=2);
  assert.ok(a.detectRegimes(long).every(x=>x.durationDays>=a.ANALYSIS_POLICY.minimumRegimeDays));
});

test('a lower low or higher high inside validity window replaces the earlier extreme',()=>{
  const a=analysis(),low=segments([[4,-3],[1,2],[1,-4],[5,3]]),high=segments([[4,3],[1,-2],[1,4],[5,-3]]);
  const lows=a.detectPivots(low),highs=a.detectPivots(high);
  assert.ok(lows.some(x=>x.pivotValue===Math.min(...low.map(r=>r.value))));
  assert.ok(highs.some(x=>x.pivotValue===Math.max(...high.map(r=>r.value))));
});

test('reference relevance includes five months before and twenty days after, but excludes seven months before',()=>{
  const a=analysis(),pivot={pivotDate:'2020-01-01',pivotValue:1,previousRegime:'falling',nextRegime:'rising',confirmationDate:'2020-04-01',durationBefore:120,durationAfter:120,confirmed:true},cycle={startDate:'2020-06-01',peakDate:'2020-08-01',troughDate:'2020-10-01'};
  assert.ok(a.resultForReference([pivot],'START','2020-06-01',cycle));
  assert.equal(a.resultForReference([pivot],'PEAK','2020-08-01',cycle),null);
  assert.ok(a.resultForReference([{...pivot,pivotDate:'2020-10-21'}],'TROUGH','2020-10-01',cycle));
});

test('duration scoring caps at one hundred and overall scoring does not punish one strong reference',()=>{
  const a=analysis();assert.equal(Math.round(a.durationScore(4,12)),33);assert.equal(Math.round(a.durationScore(10,12)),83);assert.equal(a.durationScore(12,12),100);assert.equal(a.durationScore(18,12),100);
  assert.ok(a.overallScore([{score:90}])>a.overallScore([{score:30},{score:30},{score:30}]));
});

test('current analysis separates a recent unconfirmed turn from confirmed pivots',()=>{
  const a=analysis(),rows=segments([[7,3],[2,-4]]),result=a.analyzeCurrent({code:'X'},rows,rows[0].time,rows.at(-1).time);
  assert.equal(result.evidence.status,'candidate');assert.equal(result.evidence.regime.confirmed,false);
});

test('current probability counts only recent confirmed pivots and drops stale evidence',()=>{
  const a=analysis(),recentRows=segments([[5,-3],[5,3]]),recent=a.analyzeCurrent({code:'X'},recentRows,recentRows[0].time,recentRows.at(-1).time),extended=[...recentRows,...segments([[8,2]]).map((row,index)=>month(recentRows.length+index,row.value))],stale=a.analyzeCurrent({code:'X'},extended,extended[0].time,extended.at(-1).time);
  assert.equal(recent.evidence.status,'confirmed');assert.equal(recent.results.length,1);assert.equal(stale.evidence.status,'watching');
  assert.ok(a.currentPivotProbability([recent]).probability>a.currentPivotProbability([stale]).probability);
});

test('low-scoring meaningful indicators remain visible and sort by score with deterministic ties',()=>{
  const a=analysis(),items=[
    {meta:{title:'나'},overallScore:10,meaningfulReferenceCount:1,maxReferenceScore:10,visible:true},
    {meta:{title:'가'},overallScore:80,meaningfulReferenceCount:1,maxReferenceScore:80,visible:true}
  ];assert.deepEqual(items.filter(x=>x.visible).sort(a.compareAnalyses).map(x=>x.meta.title),['가','나']);
});

test('display normalization stays separate from raw values',()=>{
  const normalized=analysis().normalizeForDisplay([month(0,10),month(1,20),month(2,30)],'2000-01-01','2000-03-01');
  assert.deepEqual(Array.from(normalized,row=>Math.round(row.value)),[0,50,100]);assert.deepEqual(Array.from(normalized,row=>row.rawValue),[10,20,30]);
});

test('selection enforces five, blocks six, clears all, and promotes the highest-ranked remaining item',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-selection.js','MacroWatchHistoricalIndicatorSelection'),state=api.create(),items=['A','B','C','D','E','F'].map(code=>({meta:{code}}));state.reconcile(items);
  for(const code of ['A','B','C','D','E'])assert.equal(state.toggle(code,true).blocked,false);
  assert.equal(state.toggle('F',true).blocked,true);state.activate('C');state.toggle('C',false);assert.equal(state.snapshot().active,'A');assert.equal(state.clear().checked.length,0);
});

test('market scope metadata drives KOSPI and US catalog membership',()=>{
  const registry={allSeries:[{code:'US2Y',marketScope:'US'},{code:'KR3Y',marketScope:'KR'},{code:'WTI',marketScope:'GLOBAL'}]},window={MacroWatchEconomicSeriesRegistry:registry};
  const api=load('assets/js/historical-insight/historical-indicator-data.js','MacroWatchHistoricalIndicators',window),repo=api.createRepository({});
  assert.deepEqual(Array.from(repo.catalog('SP500'),x=>x.code),['US2Y','WTI']);assert.deepEqual(Array.from(repo.catalog('NASDAQ_COMPOSITE'),x=>x.code),['US2Y','WTI']);assert.deepEqual(Array.from(repo.catalog('KOSPI'),x=>x.code),['US2Y','KR3Y','WTI']);
});

test('indicator data spans twenty-four months without changing chart viewport',()=>{
  const a=analysis();assert.deepEqual({...a.displayWindow({searchStart:'2018-01-01'},{startDate:'2020-03-31',troughDate:'2022-12-31'},'2026-09-15')},{from:'2018-03-31',to:'2024-12-31'});
  const controller=read('assets/js/historical-insight/historical-insight.js'),chart=read('assets/js/historical-insight/historical-index-chart.js');
  assert.doesNotMatch(controller,/checked\.length\)chart\?\.focus/);assert.match(chart,/getVisibleRange/);assert.match(chart,/setVisibleRange/);
});

test('UI exposes badges, max selection, clear all, normalized left scale and faded inactive series',()=>{
  const html=read('historical-insight.html'),css=read('assets/css/historical-insight.css'),chart=read('assets/js/historical-insight/historical-index-chart.js'),controller=read('assets/js/historical-insight/historical-insight.js');
  assert.match(html,/historical-indicator-clear/);assert.doesNotMatch(html,/data-indicator-strength/);assert.match(css,/data-reference="START"/);assert.match(css,/data-reference="PEAK"/);assert.match(css,/data-reference="TROUGH"/);
  assert.match(chart,/leftPriceScale: \{ visible: true/);assert.match(chart,/rgba\(color,\.3\)/);assert.match(chart,/subscribeClick/);assert.match(controller,/최대 5개/);
  assert.doesNotMatch(controller,/leading|coincident|lagging|trendConsistency|FILTER_THRESHOLDS/);
});

test('coverage view remains canonical, security-invoker, and read-only',()=>{
  const sql=read('supabase/migrations/20260915144500_add_economic_series_coverage.sql');assert.match(sql,/economic_chart_series_coverage\s*\nwith \(security_invoker = true\)/);assert.match(sql,/economic_chart_series_points/);assert.match(sql,/grant select[^;]*authenticated, service_role/);
});

test('current regime title setting remains administrator-update only',()=>{
  const sql=read('supabase/migrations/20260915151000_add_historical_current_settings.sql');assert.match(sql,/current_name text not null default '현재 국면 관찰 중'/);assert.match(sql,/Administrators update historical current settings/);assert.doesNotMatch(sql,/grant insert[^;]*authenticated/);
});
