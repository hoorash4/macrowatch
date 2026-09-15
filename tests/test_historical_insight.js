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
  return { ...ctx.window.MacroWatchHistoricalData, core: ctx.window.MacroWatchFrontend };
}
function database(pages) {
  const calls = [];
  return { calls, from(table) {
    const call = { table }; calls.push(call);
    const q = { select(v) { call.select = v; return q; }, order(v, options) { call.order = [v,options]; return q; },
      range(a,b) { call.range = [a,b]; return q; }, eq(k,v) { call.filter = [k,v]; return q; },
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
    assert.deepEqual(c.filter,['index_code','SP500']);
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
function ui() {
  const nodes=new Map();
  const make = () => ({dataset:{}, attrs:{}, hidden:false, disabled:false,textContent:'',
    events:{}, classList:{toggle(){}}, setAttribute(k,v){this.attrs[k]=v;},
    getAttribute(k){return this.attrs[k];}, addEventListener(k,v){this.events[k]=v;}});
  for (const id of ['host','status','meta','message','retry','full-range']) nodes.set('historical-chart-'+id,make());
  const buttons=['SP500','NASDAQ_COMPOSITE','KOSPI'].map(code=>{const b=make();b.dataset.historicalIndex=code;b.attrs['aria-selected']=String(code==='NASDAQ_COMPOSITE');return b;});
  const pending=[]; const drawn=[]; let destroyed=0;
  const w={MacroWatchHistoricalData:{indices:{SP500:'S&P 500',NASDAQ_COMPOSITE:'NASDAQ Composite',KOSPI:'KOSPI'},
    createRepository:()=>({load:code=>new Promise((resolve,reject)=>pending.push({code,resolve,reject}))})},
    MacroWatchFrontend:{createSupabaseClient:()=>({}),formatDisplayNumber:String},
    MacroWatchHistoricalChart:{create:()=>({setData:rows=>drawn.push(rows),fit(){},destroy(){destroyed++;}})},
    addEventListener(){}};
  vm.runInNewContext(read('assets/js/historical-insight/historical-insight.js'),{
    window:w,document:{getElementById:id=>nodes.get(id),querySelectorAll:()=>buttons},console:{error(){}}});
  return {nodes,buttons,pending,drawn,destroyed:()=>destroyed};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('rapid switching ignores stale responses and clears previous data',async()=>{
  const f=ui();
  f.buttons[2].events.click();
  f.pending[1].resolve([{time:'1990-01-04',value:10}]); await settle();
  f.pending[0].resolve([{time:'1990-01-02',value:999}]); await settle();
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
  const f=ui(); f.pending[0].reject(new Error('offline')); await settle();
  assert.equal(f.nodes.get('historical-chart-host').dataset.state,'error');
  assert.equal(f.nodes.get('historical-chart-retry').hidden,false);
  assert.equal(f.destroyed(),1);
  f.nodes.get('historical-chart-retry').events.click();
  f.pending[1].resolve([{time:'1990-01-02',value:5}]); await settle();
  assert.equal(f.nodes.get('historical-chart-host').dataset.state,'ready');
  assert.equal(f.nodes.get('historical-chart-retry').hidden,true);
});
