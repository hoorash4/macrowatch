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
test('cycle summary gives market context and performance figures strong visual hierarchy', () => {
  const html=read('historical-insight.html'), css=read('assets/css/historical-insight.css');
  assert.match(html,/id="historical-cycle-market"/);
  assert.match(html,/class="is-rise"[\s\S]*class="is-fall"/);
  assert.doesNotMatch(html,/historical-drawdown|is-drawdown|고점 대비 낙폭/);
  assert.match(css,/\.historical-cycle-description \{[^}]*font-size: 13px/);
  assert.match(css,/\.historical-cycle-performance strong \{[^}]*font-size: 25px/);
  assert.match(css,/\.historical-cycle-performance \{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(css,/\.historical-cycle-performance article \{[^}]*grid-template-columns: auto 1fr[^}]*min-height: 72px/);
  assert.match(css,/\.historical-cycle-performance \.is-rise strong \{ color: #dc2626/);
  assert.match(css,/\.historical-cycle-performance \.is-fall strong \{ color: #2563eb/);
  assert.match(css,/\.historical-cycle-performance b \{[^}]*font-size: 15px/);
  assert.match(css,/\.historical-cycle-points article \{[^}]*grid-template-columns: auto minmax\(0, 1fr\)/);
  assert.match(css,/\.historical-cycle-points strong \{[^}]*font-size: 17px/);
  assert.match(css,/\.historical-cycle-points span \{[^}]*font-size: 15px/);
});
test('analysis tabs separate current regime from the historical case list and enlarge their labels', () => {
  const html=read('historical-insight.html'), css=read('assets/css/historical-insight.css'), controller=read('assets/js/historical-insight/historical-insight.js');
  assert.match(html,/data-historical-mode="history">과거사례 분석<\/button>/);
  assert.match(html,/data-historical-mode="current">현재국면 분석<\/button>/);
  assert.match(css,/\.historical-analysis-tabs button \{[^}]*font-size: 14px/);
  assert.match(html,/id="historical-current-case-name"/);
  assert.match(html,/id="historical-indicator-accordion"[\s\S]*aria-label="비교 지표 선택"/);
  assert.doesNotMatch(html,/차트 추가 지표 샘플|샘플<\/small>/);
  assert.doesNotMatch(css,/\.historical-stage\.is-current-mode \.historical-summary \{ display: none/);
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
  assert.match(chart,/subscribeVisibleLogicalRangeChange\(schedulePivotLines\)/);
  assert.match(chart,/subscribeVisibleTimeRangeChange\(schedulePivotLines\)/);
  assert.match(chart,/new ResizeObserver\(schedulePivotLines\)/);
  assert.match(chart,/requestAnimationFrame\(\(\)=>\{pivotRenderFrame=null;renderPivotLines\(\);\}\)/);
  assert.match(css,/\.historical-indicator-legend \{[^}]*padding: 8px 16px 12px/);
  assert.match(controller,/const caseChanged=Boolean\(activeCase&&activeCase\.code!==item\.code\);if\(caseChanged\)\{clearIndicatorSelection\(\);activeIndicatorContext=null;visibleIndicators=\[\];\}/);
  assert.match(controller,/input\.type='radio'/);
  assert.doesNotMatch(controller,/historical-indicator-clear|최대 5개/);
  assert.match(controller,/badge\.className='historical-reference-badge'/);
  assert.match(controller,/activeMode==='history'\?`\$\{Math\.round\(item\.overallScore\)\}점`/);
});
function ui() {
  const nodes=new Map();
  const make = () => { const classes=new Set(); return ({dataset:{}, attrs:{}, hidden:false, disabled:false,textContent:'',
    value:'',events:{}, children:[], classList:{toggle(name,on){on?classes.add(name):classes.delete(name);},contains(name){return classes.has(name);}}, setAttribute(k,v){this.attrs[k]=v;},
    getAttribute(k){return this.attrs[k];}, addEventListener(k,v){this.events[k]=v;}, replaceChildren(){this.children=[];},
    append(...items){this.children.push(...items);}, querySelector(){return null;},focus(){}}); };
  for (const id of ['host','status','meta','message','retry','full-range','case-range']) nodes.set('historical-chart-'+id,make());
  for (const id of ['stage','case-panel','past-sidebar','current-sidebar','current-case-name','current-case-state','current-name-edit','current-name-form','current-name-input','current-name-cancel','current-name-status','toolbar-title','case-list','cycle-panel','cycle-state','cycle-name','cycle-market','search-range','cycle-description','rise','fall','drawdown','rise-days','fall-days','cycle-editor','cycle-form','start-date','peak-date','trough-date','cycle-save-status','indicator-clear','indicator-selection-message']) nodes.set(`historical-${id}`,make());
  const pointCards={}; for(const kind of ['start','peak','trough']){const card=make(),strong=make(),span=make();card.querySelector=s=>s==='strong'?strong:span;pointCards[kind]=card;}
  const buttons=['SP500','NASDAQ_COMPOSITE','KOSPI'].map(code=>{const b=make();b.dataset.historicalIndex=code;b.attrs['aria-selected']=String(code==='NASDAQ_COMPOSITE');return b;});
  const modeButtons=['history','current'].map(mode=>{const b=make();b.dataset.historicalMode=mode;b.attrs['aria-selected']=String(mode==='history');return b;});
  const caseButtons=[];
  const pending=[]; const drawn=[]; let destroyed=0;
  const client={auth:{getSession:async()=>({data:{session:{user:{id:'user-1'}}},error:null})},from:()=>{const q={select(){return q;},eq(){return q;},maybeSingle:async()=>({data:{is_admin:false},error:null})};return q;}};
  const markets=Object.fromEntries(['SP500','NASDAQ_COMPOSITE','KOSPI'].map(indexCode=>[indexCode,{caseCode:'dotcom',indexCode,startDate:'1994-06-24',peakDate:'2000-03-10',troughDate:'2002-10-09',status:'confirmed'}]));
  const definition={code:'dotcom',order:1,name:'닷컴버블',primaryIndex:'NASDAQ_COMPOSITE',comparisons:['SP500','KOSPI'],searchStart:'1994-01-01',searchEnd:'2003-03-31',summary:'기술주 사이클',markets};
  const currentMarkets=Object.fromEntries(['SP500','NASDAQ_COMPOSITE','KOSPI'].map(indexCode=>[indexCode,{caseCode:'ai_semiconductor',indexCode,startDate:'2022-10-12',peakDate:null,troughDate:null,status:'in_progress'}]));
  currentMarkets.NASDAQ_COMPOSITE={...currentMarkets.NASDAQ_COMPOSITE,peakDate:'2024-01-01',troughDate:'2024-06-01',status:'confirmed'};
  const currentDefinition={code:'ai_semiconductor',order:10,name:'AI/반도체 상승장',primaryIndex:'NASDAQ_COMPOSITE',comparisons:['SP500','KOSPI'],searchStart:'2022-06-01',searchEnd:null,summary:'현재 진행 국면',markets:currentMarkets};
  const w={MacroWatchHistoricalData:{indices:{SP500:'S&P 500',NASDAQ_COMPOSITE:'NASDAQ Composite',KOSPI:'KOSPI'},
    createRepository:()=>{const cache=new Map();return{load:code=>{if(!cache.has(code)){const promise=new Promise((resolve,reject)=>pending.push({code,resolve,reject}));cache.set(code,promise);promise.catch(()=>cache.delete(code));}return cache.get(code);}};}},
    MacroWatchHistoricalCycles:{createRepository:()=>({load:async()=>[currentDefinition,definition],loadCurrentSettings:async()=>({currentName:'현재 국면 관찰 중'}),saveCurrentName:async(_code,value)=>value}),marketCycle:(item,code)=>item.markets[code],calculate:()=>({start:{time:'1994-06-24',value:1},peak:{time:'2000-03-10',value:2},trough:{time:'2002-10-09',value:1},rise:100,fall:-50,drawdown:50,riseDays:1,fallDays:1}),chartPoints:()=>[]},
    MacroWatchFrontend:{createSupabaseClient:()=>client,formatDisplayNumber:String},
    MacroWatchHistoricalChart:{create:()=>({setData:rows=>drawn.push(rows),setCycle(){},focus(){},fit(){},destroy(){destroyed++;}})},
    addEventListener(){}};
  vm.runInNewContext(read('assets/js/historical-insight/historical-insight.js'),{
    window:w,document:{getElementById:id=>nodes.get(id),querySelectorAll:s=>s==='[data-historical-index]'?buttons:s==='[data-historical-mode]'?modeButtons:caseButtons,
      querySelector:s=>pointCards[s.match(/"(start|peak|trough)"/)?.[1]],createElement:()=>{const item=make();const append=item.append;item.append=(...values)=>{append.call(item,...values);if(values.length===2&&values[0].textContent)caseButtons.push(item);};return item;}},console:{error(){}}});
  return {nodes,buttons,modeButtons,caseButtons,pending,drawn,destroyed:()=>destroyed};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('rapid switching ignores stale responses and clears previous data',async()=>{
  const f=ui(); await settle();
  f.buttons[2].events.click();
  f.pending[0].resolve([{time:'1990-01-02',value:999}]);
  f.pending[1].resolve([{time:'1990-01-04',value:10}]); await settle();
  assert.equal(f.drawn.at(-1)[0].value,10);
  assert.equal(f.nodes.get('historical-chart-host').dataset.state,'ready');
  assert.match(f.nodes.get('historical-chart-meta').textContent,/KOSPI/);
  f.buttons[0].events.click();
  assert.equal(f.drawn.at(-1).length,0);
  assert.equal(f.nodes.get('historical-chart-host').dataset.state,'loading');
  f.pending[2].resolve([]); await settle();
  assert.equal(f.nodes.get('historical-chart-host').dataset.state,'empty');
  assert.equal(f.nodes.get('historical-chart-full-range').disabled,true);
});
test('request failure exposes retry and recovery restores the selected chart',async()=>{
  const f=ui(); await settle(); f.pending[0].reject(new Error('offline')); await settle();
  assert.equal(f.nodes.get('historical-chart-host').dataset.state,'error');
  assert.equal(f.nodes.get('historical-chart-retry').hidden,false);
  assert.equal(f.destroyed(),1);
  f.nodes.get('historical-chart-retry').events.click();
  f.pending[1].resolve([{time:'1990-01-02',value:5}]); await settle();
  assert.equal(f.nodes.get('historical-chart-host').dataset.state,'ready');
  assert.equal(f.nodes.get('historical-chart-retry').hidden,true);
});
test('current mode keeps the common analysis layout and replaces the left list with current indicators',async()=>{
  const f=ui(); await settle();
  assert.equal(f.caseButtons.length,1);
  assert.equal(f.caseButtons[0].children[0].textContent,'닷컴버블');
  f.pending[0].resolve([{time:'1990-01-02',value:5}]); await settle();
  f.modeButtons[1].events.click(); await settle();
  assert.equal(f.nodes.get('historical-past-sidebar').hidden,true);
  assert.equal(f.nodes.get('historical-current-sidebar').hidden,false);
  assert.equal(f.nodes.get('historical-current-case-name').textContent,'AI/반도체 상승장');
  assert.equal(f.nodes.get('historical-current-case-state').textContent,'진행 중');
  assert.equal(f.nodes.get('historical-cycle-panel').hidden,true);
  assert.equal(f.nodes.get('historical-toolbar-title').textContent,'현재 국면 차트');
  assert.match(f.nodes.get('historical-chart-meta').textContent,/AI\/반도체 상승장/);
  assert.equal(f.nodes.get('historical-stage').classList.contains('is-current-mode'),true);
});
