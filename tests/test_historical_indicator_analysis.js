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

test('regime, relevance, scale, and scoring policies are centralized without obsolete rules',()=>{
  const a=analysis(),source=read('assets/js/historical-insight/historical-indicator-analysis.js');
  assert.deepEqual({...a.ANALYSIS_POLICY.minimumRegimeDays},{short:31,medium:61,long:92});
  assert.deepEqual({...a.ANALYSIS_POLICY.relevanceMonths},{before:3,after:1});
  assert.deepEqual({...a.ANALYSIS_POLICY.correction},{maximumRetracementFraction:.5,maximumVolatilityUnits:3});
  assert.deepEqual({...a.ANALYSIS_POLICY.referenceWeights},{timing:.6,duration:.4});
  assert.doesNotMatch(source,/shortBefore|longBefore|beforeMonths|relevanceDays/);
  assert.doesNotMatch(source,/monthlySamples|stateStarts|extremeForBoundary|directionalChangeThreshold|\.025/);
  assert.doesNotMatch(source,/COVID|US2Y|RETAIL|2020-|2021-|2022-|코로나|소매/);
});

test('six-month rise, one-month correction, and a new high remain one rising regime',()=>{
  const a=analysis(),path=a.detectRetrospectiveRegimes(segments([[6,3],[1,-2],[4,3]]),{frequency:'M',minimumRegimeDays:92});
  assert.deepEqual(Array.from(path.regimes,x=>x.type),['rising']);assert.equal(path.pivots.length,0);assert.equal(path.pending,null);
});

test('six-month fall, one-month rebound, and a new low remain one falling regime',()=>{
  const a=analysis(),path=a.detectRetrospectiveRegimes(segments([[6,-3],[1,2],[4,-3]]),{frequency:'M',minimumRegimeDays:92});
  assert.deepEqual(Array.from(path.regimes,x=>x.type),['falling']);assert.equal(path.pivots.length,0);
});

test('a long shallow correction that makes a new high remains one retrospective rising regime',()=>{
  const a=analysis(),rows=segments([[8,5],[4,-1],[10,5]]),past=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:92}),online=a.detectOnlineState(rows,{frequency:'M',minimumRegimeDays:92});
  assert.deepEqual(Array.from(past.regimes,x=>x.type),['rising']);assert.equal(past.pivots.length,0);assert.equal(online.pivots.length,0);assert.ok(online.invalidations.some(item=>item.reason==='higher_high'));
});

test('a volatility-scaled deep reversal remains an independent retrospective regime',()=>{
  const a=analysis(),path=a.detectRetrospectiveRegimes(segments([[8,5],[4,-10],[7,8]]),{frequency:'M',minimumRegimeDays:92});
  assert.ok(path.regimes.some(regime=>regime.type==='falling'));assert.ok(path.pivots.some(pivot=>pivot.previousRegime==='rising'&&pivot.nextRegime==='falling'));
});

test('a short but deep move becomes a new regime once the minimum duration is met',()=>{
  const a=analysis(),rows=segments([[6,5],[2,-15],[3,-5]]),path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:31}),pivot=path.pivots.find(item=>item.previousRegime==='rising'&&item.nextRegime==='falling');
  assert.ok(pivot);assert.equal(pivot.pivotValue,Math.max(...rows.map(row=>row.value)));assert.ok(pivot.durationAfter>=31);
});

test('sustained boxes form rising-to-sideways and falling-to-sideways boundary pivots',()=>{
  const a=analysis(),risingRows=segments([[6,3],[5,0]]),fallingRows=segments([[5,-3],[5,0]]),rising=a.detectRetrospectiveRegimes(risingRows,{frequency:'M',minimumRegimeDays:92}),falling=a.detectRetrospectiveRegimes(fallingRows,{frequency:'M',minimumRegimeDays:92});
  for(const path of [rising,falling]){assert.equal(path.pivots.length,1);assert.equal(path.pivots[0].nextRegime,'sideways');assert.equal(path.pivots[0].pivotType,'boundary');}
  assert.equal(rising.pivots[0].previousRegime,'rising');assert.equal(falling.pivots[0].previousRegime,'falling');
  assert.equal(rising.pivots[0].pivotValue,Math.max(...risingRows.map(row=>row.value)));assert.equal(falling.pivots[0].pivotValue,Math.min(...fallingRows.map(row=>row.value)));
  const risingCandidate=rising.technicalCandidates.find(item=>item.previousRegime==='rising'&&item.nextRegime==='sideways');assert.equal(risingCandidate.pivotDate,rising.pivots[0].pivotDate);
});

test('a sideways regime pivots at the structural range breakout',()=>{
  const a=analysis(),rows=segments([[5,0],[6,5]]),path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:61}),pivot=path.pivots.find(item=>item.previousRegime==='sideways'&&item.nextRegime==='rising');
  assert.ok(pivot);assert.equal(pivot.pivotType,'boundary');assert.ok(pivot.pivotDate>=rows[5].time);
});

test('market trend duration selects one, two, or three-month indicator regimes independently',()=>{
  const a=analysis();assert.equal(a.requiredMinimumRegimeDays(60),31);assert.equal(a.requiredMinimumRegimeDays(120),61);assert.equal(a.requiredMinimumRegimeDays(240),92);
});

test('every market anchor uses exactly three months before and one month after',()=>{
  const a=analysis(),expected={from:'2020-03-15',to:'2020-07-15',before:92,after:30};
  for(const type of a.REFERENCE_ORDER)assert.deepEqual({...a.relevanceWindow('2020-06-15')},expected,type);
});

test('reference candidates are searched inside the anchor window and validated against the broad retrospective path',()=>{
  const a=analysis(),rows=segments([[10,-4],[10,5]]),referenceDate=rows[10].time,cycle={startDate:referenceDate,peakDate:rows.at(-1).time,troughDate:null};
  const result=a.resultForReference(rows,{frequency:'M'},'START',referenceDate,cycle),outside=a.resultForReference(rows,{frequency:'M'},'START',rows.at(-1).time,{...cycle,startDate:rows.at(-1).time});
  assert.ok(result.result);assert.equal(result.result.pivotRole,'market-relevant');assert.ok(result.result.confirmationDate>a.relevanceWindow(referenceDate).to);
  assert.equal(outside.result,null);assert.ok(result.technicalPivots.length>0);
});

test('historical validation uses observations beyond the case search range',()=>{
  const a=analysis(),rows=segments([[10,-4],[10,5]]),cycle={startDate:rows[10].time,peakDate:rows.at(-1).time,troughDate:null},item={searchStart:rows[3].time,searchEnd:rows[11].time},result=a.analyzeHistorical({code:'X',title:'X',frequency:'M'},rows,item,cycle);
  assert.ok(result.byReference.START);assert.ok(result.byReference.START.confirmationDate>item.searchEnd);
});

test('a provisional reversal that resumes the old trend remains a technical candidate and is not market relevant',()=>{
  const a=analysis(),rows=segments([[8,5],[4,-1],[10,5]]),referenceDate=rows[8].time,cycle={startDate:referenceDate,peakDate:rows.at(-1).time,troughDate:null},discovery=a.discoverReferenceCandidates(rows,referenceDate,{frequency:'M',minimumRegimeDays:92}),result=a.resultForReference(rows,{frequency:'M'},'START',referenceDate,cycle);
  assert.ok(discovery.candidates.length>0);assert.equal(result.result,null);
});

test('historical analysis separates technical pivots from market-relevant pivots and leaves unmatched anchors null',()=>{
  const a=analysis(),rows=segments([[6,-4],[6,5]]),cycle={startDate:rows[7].time,peakDate:rows.at(-1).time,troughDate:null},item={searchStart:rows[0].time,searchEnd:rows.at(-1).time},result=a.analyzeHistorical({code:'X',title:'X',frequency:'M'},rows,item,cycle);
  assert.ok(result.technicalPivots.length>0);assert.ok(result.technicalPivots.every(pivot=>pivot.pivotRole==='technical'));assert.ok(result.marketRelevantPivots.every(pivot=>pivot.pivotRole==='market-relevant'));assert.equal(result.marketRelevantPivots.length,1);assert.equal(result.byReference.PEAK,null);assert.equal(result.byReference.TROUGH,null);
});

test('duration scoring caps at one hundred and overall scoring does not punish one strong reference',()=>{
  const a=analysis();assert.equal(Math.round(a.durationScore(4,12)),33);assert.equal(Math.round(a.durationScore(10,12)),83);assert.equal(a.durationScore(12,12),100);assert.equal(a.durationScore(18,12),100);
  assert.ok(a.overallScore([{score:90}])>a.overallScore([{score:30},{score:30},{score:30}]));
});

test('current engine exposes watch, candidate, confirmation, and continuation invalidation',()=>{
  const a=analysis(),watchRows=segments([[6,3],[6,-1]]),candidateRows=segments([[6,3],[3,-20]]),resumedRows=segments([[6,3],[2,-4],[2,30]]),confirmedRows=segments([[6,-3],[4,15]]);
  const run=rows=>a.analyzeCurrent({code:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time);
  assert.equal(run(watchRows).evidence.status,'watch');assert.equal(run(candidateRows).evidence.status,'candidate');
  const resumed=run(resumedRows);assert.deepEqual(Array.from(resumed.regimes,x=>x.type),['rising']);assert.equal(resumed.evidence.status,'watching');assert.equal(resumed.evidence.contribution,0);assert.ok(resumed.evidence.invalidations.some(x=>x.reason==='higher_high'));
  const confirmed=run(confirmedRows);assert.equal(confirmed.evidence.status,'confirmed');assert.equal(confirmed.results.length,1);
});

test('elapsed time alone neither ends an online trend nor confirms a pivot',()=>{
  const a=analysis(),flatAfterHigh=segments([[6,3],[8,0]]),shallowCorrection=segments([[6,3],[8,-.5]]),flat=a.analyzeCurrent({code:'X',frequency:'M'},flatAfterHigh,flatAfterHigh[0].time,flatAfterHigh.at(-1).time),shallow=a.analyzeCurrent({code:'X',frequency:'M'},shallowCorrection,shallowCorrection[0].time,shallowCorrection.at(-1).time);
  assert.equal(flat.evidence.status,'watching');assert.equal(flat.pivots.length,0);assert.equal(shallow.evidence.status,'watch');assert.equal(shallow.pivots.length,0);
});

test('online sideways state requires a structural range breakout before trend confirmation',()=>{
  const a=analysis(),candidateRows=segments([[6,0],[3,20]]),confirmedRows=segments([[6,0],[5,10]]),candidate=a.analyzeCurrent({code:'X',frequency:'M'},candidateRows,candidateRows[0].time,candidateRows.at(-1).time),confirmed=a.analyzeCurrent({code:'X',frequency:'M'},confirmedRows,confirmedRows[0].time,confirmedRows.at(-1).time);
  assert.equal(candidate.evidence.status,'candidate');assert.equal(candidate.evidence.pending.previousRegime,'sideways');assert.equal(confirmed.evidence.status,'confirmed');assert.equal(confirmed.evidence.pivot.previousRegime,'sideways');assert.equal(confirmed.evidence.pivot.nextRegime,'rising');
});

test('watch retains a recent confirmed contribution and probability rolls provisional evidence back once',()=>{
  const a=analysis(),rows=segments([[6,-5],[4,12],[3,-20]]),current=a.analyzeCurrent({code:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time),probability=a.currentPivotProbability([current]);
  assert.equal(current.evidence.status,'watch');assert.ok(current.evidence.retainedPivot);assert.equal(current.evidence.contribution,a.ANALYSIS_POLICY.contribution.watchRetained);assert.equal(probability.probability,100);
});

test('a long low-volatility plateau followed by a new low stays inside one retrospective fall',()=>{
  const a=analysis(),rows=[];for(let i=0;i<213;i++){const d=new Date(Date.UTC(2000,0,1+i));let value;if(i<31)value=1.8-i*.004;else if(i<145)value=1.56+(i%7)*.001;else value=Math.max(.2,1.56-(i-145)*.035);rows.push({time:d.toISOString().slice(0,10),value});}
  const path=a.detectRetrospectiveRegimes(rows,{frequency:'D',minimumRegimeDays:31});
  assert.deepEqual(Array.from(path.regimes,item=>item.type),['falling']);assert.equal(path.pivots.length,0);
});

test('a monotonic monthly rise stays one regime without intermediate pivots',()=>{
  const a=analysis(),rows=[];for(let i=0;i<20;i++)rows.push({time:new Date(Date.UTC(2000,i,1)).toISOString().slice(0,10),value:.15+i*.12});
  const path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:92});assert.equal(path.pivots.length,0);assert.deepEqual(Array.from(path.regimes,x=>x.type),['rising']);
});

test('a monthly fall and sustained recovery use trend boundaries instead of a local midpoint',()=>{
  const a=analysis(),values=[100,100,100,96,92,88,84,80,76,82,88,94],rows=values.map((value,index)=>({time:new Date(Date.UTC(2000,index,1)).toISOString().slice(0,10),value})),path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:31});
  assert.ok(path.pivots.some(x=>x.pivotDate===rows[3].time&&x.nextRegime==='falling'));assert.ok(path.pivots.some(x=>x.pivotDate===rows[8].time&&x.previousRegime==='falling'));assert.equal(path.pivots.some(x=>x.pivotDate===rows[6].time),false);
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

test('indicator selection keeps exactly one selected series and can reset between cases',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-selection.js','MacroWatchHistoricalIndicatorSelection'),state=api.create(),items=['A','B','C','D','E','F'].map(code=>({meta:{code}}));state.reconcile(items);
  assert.equal(state.select('A').selected,'A');assert.equal(state.select('F').selected,'F');assert.equal(state.clear().selected,null);
});

test('market scope metadata drives KOSPI and US catalog membership',()=>{
  const registry={allSeries:[{code:'US2Y',marketScope:'US'},{code:'US_POLICY_RATE_MID',title:'미국 기준금리 (약 6주 간격)',marketScope:'US'},{code:'KR3Y',marketScope:'KR'},{code:'KR_POLICY_RATE',title:'한국 기준금리 (1개월 간격)',marketScope:'KR'},{code:'KR_CORE_CPI',title:'Core CPI (식료품·에너지 제외)',marketScope:'KR'},{code:'WTI',marketScope:'GLOBAL'}]},window={MacroWatchEconomicSeriesRegistry:registry};
  const api=load('assets/js/historical-insight/historical-indicator-data.js','MacroWatchHistoricalIndicators',window),repo=api.createRepository({});
  assert.deepEqual(Array.from(repo.catalog('SP500'),x=>x.code),['US2Y','US_POLICY_RATE_MID','WTI']);assert.deepEqual(Array.from(repo.catalog('NASDAQ_COMPOSITE'),x=>x.code),['US2Y','US_POLICY_RATE_MID','WTI']);assert.deepEqual(Array.from(repo.catalog('KOSPI'),x=>x.code),['US2Y','US_POLICY_RATE_MID','KR3Y','KR_POLICY_RATE','KR_CORE_CPI','WTI']);
  assert.deepEqual(Array.from(repo.catalog('KOSPI').filter(x=>['US_POLICY_RATE_MID','KR_POLICY_RATE','KR_CORE_CPI'].includes(x.code)),x=>x.title),['미국 기준금리','한국 기준금리','한국 Core CPI']);
});

test('indicator data spans twenty-four months without changing chart viewport',()=>{
  const a=analysis();assert.deepEqual({...a.displayWindow({searchStart:'2018-01-01'},{startDate:'2020-03-31',troughDate:'2022-12-31'},'2026-09-15')},{from:'2018-03-31',to:'2024-12-31'});
  const controller=read('assets/js/historical-insight/historical-insight.js'),chart=read('assets/js/historical-insight/historical-index-chart.js');
  assert.doesNotMatch(controller,/checked\.length\)chart\?\.focus/);assert.match(chart,/getVisibleRange/);assert.match(chart,/setVisibleRange/);
});

test('UI uses one radio-selected magenta indicator without dimming other series',()=>{
  const html=read('historical-insight.html'),css=read('assets/css/historical-insight.css'),chart=read('assets/js/historical-insight/historical-index-chart.js'),controller=read('assets/js/historical-insight/historical-insight.js');
  assert.doesNotMatch(html,/historical-indicator-clear|historical-indicator-count|historical-indicator-selection-message/);assert.match(controller,/input\.type='radio'/);assert.match(controller,/input\.name='historical-indicator'/);assert.match(css,/grid-template-columns: 16px minmax\(0,1fr\) auto/);assert.match(css,/accent-color: var\(--historical-indicator-color\)/);assert.match(css,/\.historical-indicator-score \{[^}]*justify-self:end/);assert.match(css,/historical-reference-badge\[data-reference="START"\]/);assert.match(css,/historical-reference-badge\[data-reference="PEAK"\]/);assert.match(css,/historical-reference-badge\[data-reference="TROUGH"\]/);
  assert.match(chart,/leftPriceScale: \{ visible: true/);assert.match(chart,/const indicatorColor='#c026d3'/);assert.doesNotMatch(chart,/rgba\(color|subscribeClick|onIndicatorActivate/);assert.doesNotMatch(controller,/최대 5개|snapshot\.active|snapshot\.checked/);
  assert.match(css,/\.historical-indicator-result-grid strong \{[^}]*font-size: 14px/);assert.match(css,/\.historical-indicator-result-grid p \{[^}]*font-size: 13px/);assert.match(controller,/card\.classList\.toggle\('is-empty',!result\)/);
  assert.match(controller,/CANDIDATE · 피봇 후보/);assert.match(controller,/WATCH · 조정 감시/);assert.match(controller,/최소 추세기간/);assert.match(chart,/item\.displayPivots\|\|item\.results/);
  assert.doesNotMatch(controller,/leading|coincident|lagging|trendConsistency|FILTER_THRESHOLDS/);
});

test('coverage view remains canonical, security-invoker, and read-only',()=>{
  const sql=read('supabase/migrations/20260915144500_add_economic_series_coverage.sql');assert.match(sql,/economic_chart_series_coverage\s*\nwith \(security_invoker = true\)/);assert.match(sql,/economic_chart_series_points/);assert.match(sql,/grant select[^;]*authenticated, service_role/);
});

test('current regime title setting remains administrator-update only',()=>{
  const sql=read('supabase/migrations/20260915151000_add_historical_current_settings.sql');assert.match(sql,/current_name text not null default '현재 국면 관찰 중'/);assert.match(sql,/Administrators update historical current settings/);assert.doesNotMatch(sql,/grant insert[^;]*authenticated/);
});
