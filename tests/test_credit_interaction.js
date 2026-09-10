const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname,'../assets/js/dashboard/dashboard-charts.js'),'utf8');
const utilities = fs.readFileSync(require('node:path').join(__dirname,'../assets/js/charts/analysis-chart-utils.js'),'utf8');
const code = source.slice(source.indexOf('function toCreditStressNumber'), source.indexOf('async function loadCreditStressComponentsDashboard'));
function harness(rows) {
  const nodes = new Map();
  const node = key => {
    if (!nodes.has(key)) nodes.set(key,{innerHTML:'',textContent:'',attrs:{},events:{},clientWidth:500,scrollLeft:0,
      classList:{add(){},remove(){}},setAttribute(k,v){this.attrs[k]=v;},insertAdjacentHTML(_position,html){this.innerHTML+=html;},
      querySelector:k=>k.startsWith('[data-credit-latest=') && !node('chart').innerHTML.includes(k.slice(1,-1)) ? null : node(k),querySelectorAll:()=>[],addEventListener(k,fn){this.events[k]=fn;},
      getBoundingClientRect:()=>({left:0,width:1776})});
    return nodes.get(key);
  };
  const chart=node('chart'); chart.clientWidth=808;
  let resize;
  const context={document:{getElementById:()=>chart},window:{},
    ResizeObserver:class{constructor(fn){resize=fn;} observe(){} disconnect(){}},
    requestAnimationFrame:fn=>{fn();return 1;},CREDIT_STRESS_HISTORY_MONTHS:37,CREDIT_STRESS_CHART_HEIGHT:280,
    monotoneSeriesPath:(rows,x,y)=>rows.map(r=>x(r)+','+y(r)).join(' ')};
  vm.createContext(context); vm.runInContext(utilities,context); context.window.MacroWatchAnalysisChart.scrollToLatest=()=>{};
  context.formatChartNumber = context.window.MacroWatchAnalysisChart.formatChartNumber;
  context.window.MacroWatchAnalysisChart.mountChartFrame=({container,plotMarkup})=>{
    container.innerHTML=plotMarkup;
    const frame=node('[data-history-scroll]'), svg=node('svg');
    frame.innerHTML=plotMarkup; frame.querySelector=()=>svg;
    return {frame,svg};
  };
  context.window.MacroWatchAnalysisChart.updateFixedAxis=(axis,markup)=>{axis.innerHTML=markup;};
  vm.runInContext(code,context);
  context.renderCreditStressComponents(rows);
  return {chart,node,resize};
}
test('credit chart reuses canonical frame and exposes three values without inventing missing bankruptcy data',()=>{
  const h=harness([{month:'2026-07-01',high_yield_oas_pct:3,financial_conditions_credit_index:-.2,business_bankruptcy_filings:90},
    {month:'2026-08-01',high_yield_oas_pct:4,financial_conditions_credit_index:-.1,business_bankruptcy_filings:120},
    {month:'2026-09-03',is_latest:true,high_yield_oas_pct:5,financial_conditions_credit_index:0,business_bankruptcy_filings:null}]);
  assert.doesNotMatch(h.chart.innerHTML,/korea-earnings-chart-frame/);
  assert.match(h.chart.innerHTML,/clipPath id="credit-risk-plot-clip"/);
  assert.match(h.chart.innerHTML,/clip-path="url\(#credit-risk-plot-clip\)"/);
  h.resize();
  h.node('[data-history-scroll]').events.pointermove({clientX:1776});
  assert.match(h.node('[data-credit-cursor-label]').innerHTML,/하이일드 스프레드: 5%p/);
  assert.match(h.node('[data-credit-cursor-label]').innerHTML,/기업 파산보호 신청\(3개월 평균\): 미발표/);
  assert.equal(h.node('[data-credit-cursor-date]').textContent,'2026-09-03 (잠정치)');
  assert.doesNotMatch(h.chart.innerHTML,/NaN|Infinity/);
});
test('all-null series and a single observation retain finite chart coordinates',()=>{
  const h=harness([{month:'2026-08-01',high_yield_oas_pct:null,financial_conditions_credit_index:null,business_bankruptcy_filings:null}]);
  h.resize();
  assert.doesNotMatch(h.chart.innerHTML,/NaN|Infinity/);
  assert.doesNotMatch(h.node('[data-axis-side="left"]').innerHTML,/NaN|Infinity/);
});
test('credit chart grid always follows the left axis and ignores right-axis ticks',()=>{
  assert.match(source, /initialLeftTicks = \[\.\.\.highYieldScale\.ticks\]\.reverse\(\)/);
  assert.match(source, /data-credit-y-grid/);
  assert.match(source, /\[\.\.\.scales\[0\]\.ticks\]\.reverse\(\)\.forEach/);
  assert.match(source, /yGridLines\.forEach\(\(line\) => line\.setAttribute\('visibility', 'hidden'\)\)/);
  assert.match(source, /alignedRightTicksFor\(scales\[0\],scales\[2\]/);
  assert.doesNotMatch(source, /const grids = Array\.from\(\{length:5\}/);
});
