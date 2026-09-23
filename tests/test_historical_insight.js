const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');
const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
function api(client) {
  const ctx = { window: { MACROWATCH_CONFIG: { supabaseUrl: '', supabasePublishableKey: '' } } };
  vm.runInNewContext(read('assets/js/core/frontend-core.js'), ctx);
  vm.runInNewContext(read('assets/js/historical-insight/historical-index-data.js'), ctx);
  vm.runInNewContext(read('assets/js/historical-insight/historical-cycle-data.js'), ctx);
  return { ...ctx.window.MacroWatchHistoricalData, cycles: ctx.window.MacroWatchHistoricalCycles, core: ctx.window.MacroWatchFrontend };
}
function database(pages) {
  const calls = [];
  return { calls, from(table) {
    const call = { table }; calls.push(call);
    const q = { select(v) { call.select = v; return q; }, order(v, options) { call.order = [v,options]; return q; },
      range(a,b) { call.range = [a,b]; return q; }, eq(k,v) { (call.filters ||= []).push([k,v]); return q; },
      gte(k,v) { call.lower = [k,v]; return q; }, then(resolve,reject) { return Promise.resolve(pages.shift()).then(resolve,reject); } };
    return q;
  } };
}
const row = (n, close = 100) => ({ market_date: new Date(Date.UTC(1990,0,2+n)).toISOString().slice(0,10), close });
test('the separate one-line case footer ranks the sum of three stored index scores', async () => {
  const html=read('historical-insight.html'),css=read('assets/css/historical-insight.css'),source=read('assets/js/historical-insight/historical-insight.js');
  assert.match(html,/class="historical-cycle-points"[\s\S]*?<\/section>\s*<section id="historical-indicator-detail"[^>]*><\/section>\s*<section id="historical-cycle-top-indicators"[\s\S]*id="historical-cycle-top-list"/);
  assert.match(css,/\.historical-cycle-top-strip \{[^}]*display: flex/);
  assert.match(css,/\.historical-cycle-top-indicators ol \{[^}]*display: flex/);
  const from=source.indexOf('  function candidatesFor('),to=source.indexOf('  function classifyStoredPivots(',from);
  const panel={hidden:true},list={children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);}};
  const document={createElement:tag=>({tag,textContent:'',children:[],append(...children){this.children.push(...children);}})};
  const metas=['A','B','C','D'].map(code=>({code,title:code}));
  const scores={SP500:{A:79,B:92,C:60,D:79},NASDAQ_COMPOSITE:{A:10,B:50,C:20,D:90},KOSPI:{A:20,B:1,C:10,D:4}};
  const scope={$:id=>id==='historical-cycle-top-indicators'?panel:list,document,
    indicatorData:{SCORE_VERSION:'v9'},hiddenIndicatorCodes:new Set(),
    indicatorRepository:{catalog:()=>metas,loadScoreRows:async(caseCode,indexCode)=>{
      assert.equal(caseCode,'tightening_2022');
      return new Map(Object.entries(scores[indexCode]).map(([code,score])=>[code,{scoring_version:'v9',by_reference:{LIST:{score}}}]));
    }}};
  const {candidatesFor,topIndicatorTotals,renderTopIndicators}=vm.runInNewContext(`${source.slice(from,to)}\n({candidatesFor,topIndicatorTotals,renderTopIndicators})`,scope);
  const selected=candidatesFor({mode:'history',analyses:metas.map(meta=>({meta,byReference:{LIST:{score:scores.SP500[meta.code]}}}))}).items;
  assert.equal(selected[0].meta.code,'B');
  renderTopIndicators(await topIndicatorTotals('tightening_2022'));
  assert.equal(panel.hidden,false);
  assert.deepEqual(list.children.map(row=>row.children.map(cell=>cell.textContent)),[['01','D','총 173점'],['02','B','총 143점'],['03','A','총 109점']]);
  renderTopIndicators([]);
  assert.equal(panel.hidden,true);
});
test('all pages use the canonical index filter and start boundary; cache avoids duplicate reads', async () => {
  const db = database([{ data: Array.from({length:1000},(_,i)=>row(i)) }, {data:[row(1000)]}]);
  const repo = api().createRepository(db);
  const rows = await repo.load('SP500');
  assert.equal(rows.length,1001);
  assert.equal(rows[1000].time,row(1000).market_date);
  await repo.load('SP500');
  assert.equal(db.calls.length,2);
  assert.deepEqual(db.calls.map(c=>c.range),[[0,999],[1000,1999]]);
  for (const c of db.calls) {
    assert.equal(c.table,'market_index_prices');
    assert.deepEqual(c.filters,[['index_code','SP500']]);
    assert.deepEqual(c.lower,['market_date','1990-01-01']);
  }
});
test('a later page failure returns no partial history and retry refetches', async () => {
  const db = database([{ data:Array.from({length:1000},(_,i)=>row(i)) }, {error:new Error('offline')}, {data:[row(0)]}]);
  const repo=api().createRepository(db);
  await assert.rejects(repo.load('KOSPI'), /offline/);
  assert.equal((await repo.load('KOSPI')).length,1);
  assert.deepEqual(db.calls[2].range,[0,999]);
});
test('empty data stays empty and invalid source rows never become zero or disappear silently', async () => {
  const a=api();
  assert.equal((await a.createRepository(database([{data:[]}])).load('NASDAQ_COMPOSITE')).length,0);
  for (const rows of [[row(0,null)],[row(0,'')],[row(0,Infinity)],[row(0,0)],[row(1),row(0)],[row(0),row(0)],[{market_date:'2026-02-30',close:1}]]) {
    assert.throws(()=>a.normalize(rows), /원천 데이터/);
  }
  await assert.rejects(a.createRepository(database([])).load('NASDAQ100'), /지원하지/);
});
const caseRow = overrides => ({ case_code:'dotcom', display_order:1, case_name:'닷컴버블', primary_index_code:'NASDAQ_COMPOSITE',
  comparison_index_codes:['SP500','KOSPI'], search_start:'1994-01-01', search_end:'2003-03-31', cycle_summary:'기술주 사이클', ...overrides });
const marketRow = overrides => ({case_code:'dotcom',index_code:'NASDAQ_COMPOSITE',start_date:'1994-06-24',
  peak_date:'2000-03-10',trough_date:'2002-10-09',cycle_status:'confirmed',...overrides});
test('case definitions keep separate market cycles and derive each market performance', async () => {
  const a=api(), db=database([{data:[caseRow()]},{data:[
    marketRow(),marketRow({index_code:'KOSPI',start_date:'1998-06-16',peak_date:'2000-01-04',trough_date:'2001-09-17'})
  ]}]);
  const cases=await a.cycles.createRepository(db).load();
  assert.equal(cases[0].searchStart,'1994-01-01');
  assert.deepEqual(Array.from(cases[0].comparisons),['SP500','KOSPI']);
  assert.equal(a.cycles.marketCycle(cases[0],'KOSPI').startDate,'1998-06-16');
  const prices=[{time:'1994-06-24',value:693.79},{time:'2000-03-10',value:5048.6201},{time:'2002-10-09',value:1114.11}];
  const metrics=a.cycles.calculate(cases[0],a.cycles.marketCycle(cases[0],'NASDAQ_COMPOSITE'),prices);
  assert.ok(Math.abs(metrics.rise-627.68)<.01);
  assert.ok(Math.abs(metrics.fall+77.93)<.01);
  assert.equal(metrics.drawdown,Math.abs(metrics.fall));
  assert.equal(metrics.riseDays,2086);
  assert.equal(metrics.fallDays,943);
  assert.equal(db.calls[0].table,'historical_cases');
  assert.equal(JSON.stringify(db.calls[0].order),JSON.stringify(['display_order',{ascending:true}]));
  assert.equal(db.calls[1].table,'historical_case_market_cycles');
});
test('case catalog displays the most recent market regime first', async () => {
  const a=api(), db=database([{data:[
    caseRow(),caseRow({case_code:'ai_semiconductor',display_order:10,case_name:'AI/반도체 상승장',search_start:'2022-06-01',search_end:null})
  ]},{data:[
    marketRow(),marketRow({case_code:'ai_semiconductor',start_date:'2022-12-28',peak_date:null,trough_date:null,cycle_status:'in_progress'})
  ]}]);
  const cases=await a.cycles.createRepository(db).load();
  assert.deepEqual(Array.from(cases, item => item.code),['ai_semiconductor','dotcom']);
});
test('in-progress cycles allow unconfirmed peak and trough while malformed definitions and missing closes fail', () => {
  const a=api(), item=a.cycles.normalizeCase(caseRow({case_code:'ai',case_name:'AI',search_start:'2022-06-01',search_end:null}),[
    a.cycles.normalizeMarket(marketRow({case_code:'ai',start_date:'2022-12-28',peak_date:null,trough_date:null,cycle_status:'in_progress'}))
  ]), progress=a.cycles.marketCycle(item,'NASDAQ_COMPOSITE');
  const metrics=a.cycles.calculate(item,progress,[{time:'2022-12-28',value:10213.29}]);
  assert.equal(metrics.start.value,10213.29); assert.equal(metrics.peak,null); assert.equal(metrics.rise,null);
  assert.throws(()=>a.cycles.normalizeMarket(marketRow({start_date:'2000-01-01',peak_date:'1993-01-01'})),/정의를 확인/);
  assert.throws(()=>a.cycles.calculate(item,{...progress,peakDate:'2022-01-01'},[{time:'2022-01-01',value:1},{time:'2022-12-28',value:2}]),/START → PEAK/);
  assert.throws(()=>a.cycles.calculate(item,{...progress,peakDate:'2023-01-01'},[{time:'2022-12-28',value:2}]),/기준일/);
});
test('a case can share NASDAQ pivot dates while keeping the S&P 500 as its market benchmark', () => {
  const a=api(),base=caseRow({primary_index_code:'SP500',pivot_source_index_code:'NASDAQ_COMPOSITE'});
  const market=marketRow({index_code:'SP500'});
  const item=a.cycles.normalizeCase(base,[a.cycles.normalizeMarket(market)]);
  assert.equal(item.primaryIndex,'SP500');
  assert.equal(item.pivotSourceIndex,'NASDAQ_COMPOSITE');
});
test('administrator save updates only the selected market cycle and refreshes cache', async () => {
  const a=api(); let payload=null;
  const reads=[[caseRow()],[marketRow()]];
  const client={from(table){let updating=false;const q={select(){return q;},order(){return q;},range(){return q;},eq(k,v){(q.filters ||= []).push([k,v]);return q;},
    update(value){updating=true;payload=value;return q;},single:async()=>({data:marketRow({start_date:payload.start_date,peak_date:payload.peak_date,trough_date:payload.trough_date,cycle_status:payload.cycle_status}),error:null}),
    then(resolve,reject){if(updating)return Promise.resolve({data:null,error:null}).then(resolve,reject);return Promise.resolve({data:reads.shift()||[]}).then(resolve,reject);}};q.table=table;return q;}};
  const repo=a.cycles.createRepository(client); await repo.load();
  const saved=await repo.save('dotcom','NASDAQ_COMPOSITE',{startDate:'1994-06-24',peakDate:'',troughDate:''},'user-1');
  assert.equal(payload.cycle_status,'in_progress'); assert.equal(payload.peak_date,null); assert.equal(payload.updated_by,'user-1');
  assert.equal(saved.status,'in_progress'); assert.equal((await repo.load())[0].markets.NASDAQ_COMPOSITE.peakDate,null);
  const confirmed=await repo.save('dotcom','NASDAQ_COMPOSITE',{startDate:'1994-06-24',peakDate:'2000-03-10',troughDate:'2002-10-09'},'user-1');
  assert.equal(payload.cycle_status,'confirmed'); assert.equal(confirmed.status,'confirmed');
});
test('fact view resolves canonical values without look-ahead and protects anonymous access', () => {
  const sql=read('supabase/migrations/20260915140000_add_historical_anchor_facts.sql');
  const expanded=read('supabase/migrations/20260915141500_expand_historical_anchor_rate_facts.sql');
  assert.match(sql,/historical_case_anchor_facts\s*\n\+?with \(security_invoker = true\)/);
  assert.match(sql,/economic_chart_series_points/);
  assert.match(sql,/observation_date \+ facts\.release_lag_days::integer <= anchors\.anchor_date/);
  assert.match(sql,/\('ALL', 'US_CPI',[^\n]*, 45, 120\)/);
  assert.match(sql,/revoke all on table public\.historical_case_anchor_facts from public, anon, authenticated/);
  assert.match(sql,/grant select on table public\.historical_case_anchor_facts to authenticated, service_role/);
  assert.match(expanded,/\('ALL', 'US2Y',/);
  assert.match(expanded,/\('ALL', 'US10Y',/);
  assert.match(expanded,/observation_date \+ facts\.release_lag_days::integer <= anchors\.anchor_date/);
});
test('migration stores thirty market-specific cycles with protected access', () => {
  const sql=read('supabase/migrations/20260915023946_add_historical_cycle_definitions.sql');
  const marketSql=read('supabase/migrations/20260915030500_split_historical_cycles_by_market.sql');
  assert.equal((sql.match(/^\s*\('[a-z0-9_]+', \d+,/gm)||[]).length,10);
  assert.doesNotMatch(sql,/한국 IT버블/);
  assert.equal((marketSql.match(/^\s*\('[a-z0-9_]+', '(?:SP500|NASDAQ_COMPOSITE|KOSPI)'/gm)||[]).length,30);
  assert.match(marketSql,/\('dotcom', 'KOSPI', '1998-06-16', '2000-01-04', '2001-09-17'\)/);
  assert.match(marketSql,/\('ai_semiconductor', 'SP500', '2022-10-12', null, null\)/);
  assert.match(marketSql,/revoke all on table public\.historical_case_market_cycles from anon, authenticated/);
  assert.match(marketSql,/grant select, insert, update on table public\.historical_case_market_cycles to authenticated/);
  assert.doesNotMatch(marketSql,/grant delete[^;]* to authenticated/);
  assert.match(marketSql,/on conflict \(case_code, index_code\) do nothing/);
});
test('selected historical case expands its summary and performance above the unchanged anchor cards', () => {
  const html=read('historical-insight.html'), css=read('assets/css/historical-insight.css'), controller=read('assets/js/historical-insight/historical-insight.js');
  assert.match(html,/id="historical-cycle-market"/);
  assert.match(controller,/data-historical-case-expanded/);
  assert.match(controller,/class="is-rise"[\s\S]*class="is-fall"/);
  assert.match(controller,/expanded\.querySelector\('\[data-cycle-summary\]'\)\.textContent=item\.summary/);
  assert.match(html,/id="historical-cycle-editor"[\s\S]*class="historical-cycle-points"[\s\S]*id="historical-indicator-detail"/);
  assert.doesNotMatch(html,/id="historical-cycle-description"|id="historical-rise"|id="historical-fall"/);
  assert.doesNotMatch(html,/historical-drawdown|is-drawdown|고점 대비 낙폭/);
  assert.match(css,/\.historical-case-expanded\{[^}]*padding:10px/);
  assert.match(css,/\.historical-case-expanded \.historical-cycle-description\{[^}]*border-bottom:1px solid var\(--theme-border\)[^}]*font-size:12px;font-weight:400;line-height:1\.65/);
  assert.match(css,/\.historical-case-expanded \.historical-cycle-performance\{[^}]*grid-template-columns:1fr/);
  assert.match(css,/\.historical-cycle-performance \.is-rise strong \{ color: #dc2626/);
  assert.match(css,/\.historical-cycle-performance \.is-fall strong \{ color: #2563eb/);
  assert.match(css,/\.historical-cycle-points article \{[^}]*grid-template-columns: auto minmax\(0, 1fr\)/);
  assert.match(css,/\.historical-cycle-points strong \{[^}]*font-size: 17px/);
  assert.match(css,/\.historical-cycle-points span \{[^}]*font-size: 15px/);
});
test('cycle accent continues to the primary pivot cards in red green blue order', () => {
  const css=read('assets/css/historical-insight.css'), controller=read('assets/js/historical-insight/historical-insight.js');
  assert.match(css,/\.historical-cycle-panel::before[^\n]*#dc2626 0 34%, #16a34a 34% 67%, #2563eb 67%/);
  assert.match(css,/\.historical-cycle-panel:has\(\+ \.historical-indicator-detail:not\(\[hidden\]\)\)::before/);
  assert.match(css,/\.historical-indicator-primary::before[^\n]*#16a34a 0 42%, #2563eb 42%/);
  assert.match(controller,/primary\.append\(grid\);root\.append\(primary\)/);
  assert.match(controller,/root\.append\(darkHeading,darkGrid\)/);
  assert.match(css,/\.historical-indicator-detail:has\(\.historical-indicator-primary\) \{ border-top: 0; \}/);
  assert.match(css,/\.historical-cycle-panel:has\(\+ \.historical-indicator-detail:not\(\[hidden\]\)\) \{ background: linear-gradient\(180deg/);
  assert.match(css,/\.historical-indicator-primary \{[^}]*margin: -18px -20px 0; padding: 18px 20px 0; background: linear-gradient\(180deg/);
  assert.match(css,/\.historical-indicator-primary:has\(\+ \.historical-pivot-dark-heading\) \{ margin-bottom: -14px; padding-bottom: 14px; \}/);
  assert.match(css,/\.historical-pivot-dark-heading \{[^}]*margin:14px -20px 0; padding:22px 20px 0; border-top:1px solid var\(--theme-border\)/);
});
test('historical pivot cards place their status stripe on the top edge only', () => {
  const css=read('assets/css/historical-insight.css');
  assert.match(css,/\.historical-pivot-detail-grid article \{[^}]*border-left:0; border-top:3px solid var\(--historical-indicator-color\)/);
  assert.match(css,/\.historical-pivot-detail-grid article\.is-empty \{ border-top-color:var\(--theme-border\)/);
  assert.match(css,/\.historical-pivot-detail-grid article\.is-dark \{ border-top-color:var\(--historical-near-miss-color\)/);
  assert.match(css,/\.historical-indicator-result-grid article \{[^}]*border-left: 3px solid/);
});
test('analysis tabs separate current regime from the historical case list and enlarge their labels', () => {
  const html=read('historical-insight.html'), css=read('assets/css/historical-insight.css'), controller=read('assets/js/historical-insight/historical-insight.js');
  assert.match(html,/data-historical-mode="history">과거사례 분석<\/button>/);
  assert.match(html,/data-historical-mode="current">현재국면 분석<\/button>/);
  assert.match(css,/\.historical-analysis-tabs button \{[^}]*font-size: 14px/);
  assert.match(html,/id="historical-current-case-name"/);
  assert.match(html,/id="historical-indicator-section"[\s\S]*id="historical-indicator-clear"[\s\S]*aria-label="비교 지표 선택"/);
  assert.doesNotMatch(html,/차트 추가 지표 샘플|샘플<\/small>/);
  assert.doesNotMatch(html,/class="historical-summary"|CYCLE STANDARD|HISTORICAL FACTS|CURRENT COMPARISON/);
  assert.doesNotMatch(css,/\.historical-summary/);
  assert.match(css,/\.historical-workspace:has\(> \.historical-cycle-panel:not\(\[hidden\]\)\)::after \{[^}]*flex: 0 0 24px; border-top: 1px solid var\(--theme-border\)/);
  assert.match(controller,/cases\.filter\(isHistoricalCase\)/);
  assert.match(controller,/Object\.values\(item\.markets\)\.some\(cycle\s*=>\s*cycle\.status\s*!==\s*'confirmed'\)/);
  assert.match(html,/id="historical-current-name-edit"[^>]*hidden/);
  assert.match(controller,/saveCurrentName\(currentSource\?\.code\|\|null/);
});
test('case range keeps the line continuous while placing cycle markers inside both chart edges', () => {
  const controller=read('assets/js/historical-insight/historical-insight.js');
  const chart=read('assets/js/historical-insight/historical-index-chart.js');
  assert.match(controller,/cycle\.troughDate \|\| cycle\.peakDate \|\| activeRows\.at\(-1\)\.time/);
  assert.match(controller,/chart\.focus\(from,\s*to,\s*\.12\)/);
  assert.match(chart,/span \* markerInset \/ \(1 - markerInset \* 2\)/);
  assert.match(chart,/from: data\[Math\.floor\(Math\.max\(0, startIndex - context\)\)\]\.time/);
  assert.match(chart,/to: data\[Math\.ceil\(Math\.min\(data\.length - 1, endIndex \+ context\)\)\]\.time/);
  assert.match(chart,/TROUGH: \{ position: 'belowBar', shape: 'arrowUp'/);
  assert.match(controller,/await refreshIndicators\(token\);if\(token===requestToken\)focusCase\(\)/);
});
test('single indicator selection preserves the visible calendar range and keeps its legend clear of the plot', () => {
  const controller=read('assets/js/historical-insight/historical-insight.js');
  const chart=read('assets/js/historical-insight/historical-index-chart.js');
  const css=read('assets/css/historical-insight.css');
  assert.match(chart,/const visibleRange=chart\?\.timeScale\(\)\.getVisibleRange\(\)/);
  assert.match(chart,/if\(visibleRange\)chart\.timeScale\(\)\.setVisibleRange\(visibleRange\)/);
  assert.match(chart,/class PivotLinePrimitive/);
  assert.match(chart,/line\.attachPrimitive\(primitive\)/);
  assert.match(chart,/entry\.series\.detachPrimitive\(primitive\)/);
  assert.match(chart,/class PivotTimeAxisRenderer/);
  assert.match(chart,/class PivotTimeAxisPaneView/);
  assert.match(chart,/timeAxisPaneViews\(\)\{return \[this\.timeAxisPaneView\];\}/);
  assert.match(chart,/ctx\.lineTo\(px,labelTop\)/);
  assert.match(chart,/minimumHeight: 46/);
  assert.match(chart,/axisPressedMouseMove: \{ time: true, price: false \}/);
  assert.match(css,/\.historical-chart-region \{ flex: 1 0 510px; \}/);
  assert.match(css,/@media\(max-width:850px\)\{\.historical-chart-region\{flex-basis:645px\}/);
  assert.doesNotMatch(chart,/pivotText|timingText|fillText\(this\.view\.label/);
  assert.doesNotMatch(chart,/historical-indicator-pivot-line|timeScale\(\)\.subscribeVisible/);
  assert.match(css,/\.historical-indicator-legend \{[^}]*padding: 8px 16px 12px/);
  assert.match(controller,/const caseChanged=Boolean\(activeCase&&activeCase\.code!==item\.code\);if\(caseChanged\)\{clearIndicatorSelection\(\);activeIndicatorContext=null;visibleIndicators=\[\];\}/);
  assert.match(controller,/input\.type='radio'/);
  assert.match(controller,/historical-indicator-clear/);assert.doesNotMatch(controller,/최대 5개/);
  assert.match(controller,/badge\.className='historical-reference-badge'/);
});
test('current market anchors use the persisted cycle path and force a complete signal recalculation',()=>{
  const html=read('historical-insight.html'),controller=read('assets/js/historical-insight/historical-insight.js');
  for(const id of ['historical-current-anchor-form','historical-current-start-date','historical-current-peak-date','historical-current-trough-date'])assert.match(html,new RegExp(`id="${id}"`));
  assert.match(controller,/function saveAnchors\(values,output\)/);assert.match(controller,/await caseRepository\.save\(savedCode,activeCode,values,currentUser\.id\)/);assert.match(controller,/analysisCache\.clear\(\);rebuildCurrentModel\(\)/);assert.match(controller,/historical-current-anchor-form'\)\.addEventListener\('submit'/);assert.match(controller,/showCurrentAnchors\(cycle\)/);
});
test('current indicator list includes every available series without historical score screening',()=>{
  const source=read('assets/js/historical-insight/historical-insight.js');
  assert.match(source,/usable=catalog\.filter\(item=>coverage\.has\(item\.code\)\)/);
  assert.match(source,/rawAnalyses=await mapSeries\(usable/);
  assert.doesNotMatch(source,/historicalScore|analysis\.meaningfulReferenceCount>0/);
});
test('historical indicator list keeps covered series even when no pivot has been stored',()=>{
  const source=read('assets/js/historical-insight/historical-insight.js');
  const candidates=source.match(/function candidatesFor\(context\)\{[^\n]+/)[0];
  assert.match(candidates,/context\.mode==='history'\)return\{items:\[\.\.\.context\.analyses\]\.sort/);
  assert.doesNotMatch(candidates,/context\.analyses\.filter/);
  assert.match(source,/const eligible=catalog\.filter\(item=>\{const c=coverage\.get\(item\.code\);return c&&c\.firstDate<=activeCase\.searchStart&&c\.lastDate>=end;\}\)/);
});


test('comparison indicators are narrow and limited to the Historical chart row', () => {
  const html=read('historical-insight.html');
  const css=read('assets/css/historical-insight.css');
  assert.match(html,/class="historical-workspace"[\s\S]*class="historical-chart-with-indicators"[\s\S]*class="historical-indicator-rail"/);
  assert.match(html,/historical-chart-with-indicators[\s\S]*historical-indicator-rail[\s\S]*id="historical-indicator-section"[\s\S]*<\/div>\s*<section id="historical-cycle-panel"/);
  assert.match(css,/grid-template-columns:220px minmax\(0,1fr\)/);
  assert.match(css,/\.historical-chart-with-indicators \{[^}]*grid-template-columns:minmax\(0,1fr\) 220px/);
  assert.match(css,/\.historical-indicator-list \{[^}]*max-height:510px/);
});


test('admins can add edit delete Historical cases in-page with automatic pivot and summary preview', () => {
  const html=read('historical-insight.html');
  const controller=read('assets/js/historical-insight/historical-insight.js');
  const css=read('assets/css/historical-insight.css');
  const adminControl=read('supabase/functions/admin-control/index.ts');
  assert.match(html,/id="historical-case-add"/);
  assert.match(html,/id="historical-case-modal"/);
  assert.match(html,/id="historical-case-auto-analyze"/);
  assert.match(html,/id="historical-case-delete-modal"/);
  assert.match(controller,/preview_historical_case/);
  assert.match(controller,/save_historical_case/);
  assert.match(controller,/delete_historical_case/);
  assert.match(controller,/historical-case-actions/);
  assert.match(css,/\.historical-case-modal\{/);
  assert.match(adminControl,/function historicalCycleCandidate/);
  assert.match(adminControl,/generateHistoricalSummary/);
  assert.match(adminControl,/action === "preview_historical_case"/);
  assert.match(adminControl,/action === "save_historical_case"/);
  assert.match(adminControl,/action === "delete_historical_case"/);
  assert.match(adminControl,/\.upsert\(cycles\.map/);
  assert.doesNotMatch(adminControl,/deleteCyclesError/);
});


test('Historical case edit and delete controls float over the unchanged list item on hover', () => {
  const css=read('assets/css/historical-insight.css');
  assert.match(css,/\.historical-case-row\{position:relative;display:block/);
  assert.match(css,/\.historical-case-row>\.historical-case\{width:100%\}/);
  assert.match(css,/\.historical-case-actions\{position:absolute;[^}]*right:7px;[^}]*top:50%/);
  assert.match(css,/visibility:hidden/);
  assert.match(css,/\.historical-case-row:hover \.historical-case-actions/);
});
