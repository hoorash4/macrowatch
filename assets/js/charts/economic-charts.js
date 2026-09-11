(() => {
'use strict';

const cfg = window.MACROWATCH_CONFIG || {};
const supabaseClient = window.supabase?.createClient(cfg.supabaseUrl, cfg.supabasePublishableKey);
const CHART_COLORS = { raw:'#111827', fast:'#2563eb', slow:'#d97706', grid:'#e5e7eb', text:'#6b7280', cross:'#9ca3af', line:'#dc2626', selected:'#7c3aed' };
const RIGHT_OFFSET = 7;
const MIN_VISIBLE_BARS = 12;

const SERIES = [
  { code:'US2Y', title:'미국채 2년', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2, fallback:['policy_expectation_spreads','observation_date,treasury_2y_rate','observation_date','treasury_2y_rate'] },
  { code:'US10Y', title:'미국채 10년', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2, fallback:['us_policy_rate_daily','observation_date,treasury_10y_pct','observation_date','treasury_10y_pct'] },
  { code:'US10Y2Y', title:'미국 10Y-2Y 스프레드', frequency:'D', unit:'%p', category:'금리 · 신용', decimals:2 },
  { code:'HY_OAS', title:'미국 하이일드 OAS', frequency:'D', unit:'%p', category:'금리 · 신용', decimals:2 },
  { code:'EM_OAS', title:'이머징 채권 OAS', frequency:'D', unit:'%p', category:'금리 · 신용', decimals:2 },
  { code:'KR3Y', title:'국고채 3년', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2 },
  { code:'KR10Y', title:'국고채 10년', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2 },
  { code:'KR10Y3Y', title:'국고채 10Y-3Y 스프레드', frequency:'D', unit:'%p', category:'금리 · 신용', decimals:2 },
  { code:'US3M', title:'미국채 3개월', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2, fallbackOnly:true, fallback:['policy_expectation_spreads','observation_date,treasury_3m_rate','observation_date','treasury_3m_rate'] },
  { code:'EFFR', title:'미국 유효연방기금금리', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2, fallbackOnly:true, fallback:['policy_expectation_spreads','observation_date,effr_rate','observation_date','effr_rate'] },
  { code:'FED_TARGET_UPPER', title:'미국 기준금리 상단', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2, fallbackOnly:true, fallback:['us_policy_rate_daily','observation_date,target_upper_pct','observation_date','target_upper_pct'] },
  { code:'WTI', title:'WTI 유가', frequency:'D', unit:'USD', category:'시장가격', decimals:2 },
  { code:'USDKRW', title:'원/달러 환율', frequency:'D', unit:'원', category:'시장가격', decimals:1 },
  { code:'REDBOOK', title:'Redbook Index', frequency:'W', unit:'% YoY', category:'고빈도 경기', decimals:1, pending:'자체 주간 적재 예정' },
  { code:'WEI', title:'미국 Weekly Economic Index', frequency:'W', unit:'%', category:'고빈도 경기', decimals:2 },
  { code:'KR_EXPORT_DAILY_AVG', title:'한국 10일구간 일평균 수출', frequency:'T', unit:'억달러/조업일', category:'고빈도 경기', decimals:2, pending:'관세청 구간별 적재 예정' },
];

const MA_WINDOWS = {
  D: [5, 20, '5일', '20일'],
  W: [4, 26, '4주', '26주'],
  T: [6, 18, '6구간', '18구간'],
  M: [6, 24, '6개월', '24개월'],
};

let sessionUser = null;
let activeMeta = null;
let activeRows = [];
let chart = null;
let rawSeries = null;
let fastSeries = null;
let slowSeries = null;
let resizeObserver = null;
let lineMode = false;
let lineCounter = 0;
let horizontalLines = [];
let selectedLineId = null;
let chartAlerts = [];
let applyingRange = false;

const $ = id => document.getElementById(id);

function frequencyLabel(frequency) {
  return ({D:'일별',W:'주별',T:'10일 구간',M:'월별'})[frequency] || frequency;
}

function movingAverage(rows, windowSize) {
  const result = [];
  let sum = 0;
  const queue = [];
  for (const row of rows) {
    queue.push(row.value); sum += row.value;
    if (queue.length > windowSize) sum -= queue.shift();
    if (queue.length === windowSize) result.push({ time:row.time, value:sum / windowSize });
  }
  return result;
}

async function pagedQuery(table, select, orderColumn, filters = []) {
  const rows = [];
  for (let from = 0; ; from += 1000) {
    let query = supabaseClient.from(table).select(select).order(orderColumn,{ascending:true}).range(from,from+999);
    for (const [column,value] of filters) query = query.eq(column,value);
    const {data,error} = await query;
    if (error) throw error;
    rows.push(...(data || []));
    if (!data || data.length < 1000) break;
  }
  return rows;
}

function normalizeRows(rows,dateKey,valueKey) {
  return rows.map(row => ({time:String(row[dateKey]).slice(0,10),value:Number(row[valueKey])})).filter(row => row.time.length === 10 && Number.isFinite(row.value));
}

async function fetchSeries(meta) {
  if (!meta.fallbackOnly) {
    const primary = await pagedQuery('economic_chart_points','observation_date,value','observation_date',[['series_code',meta.code]]);
    const normalized = normalizeRows(primary,'observation_date','value');
    if (normalized.length) return normalized;
  }
  if (!meta.fallback) return [];
  const [table,select,dateKey,valueKey] = meta.fallback;
  return normalizeRows(await pagedQuery(table,select,dateKey),dateKey,valueKey);
}

function formatValue(value, meta=activeMeta) {
  if (!meta || !Number.isFinite(Number(value))) return '—';
  return `${Number(value).toFixed(meta.decimals)}${meta.unit ? ` ${meta.unit}` : ''}`;
}

function chartOptions() {
  return {
    layout:{ background:{color:'#fff'}, textColor:CHART_COLORS.text, fontFamily:'Pretendard, system-ui, sans-serif', fontSize:11, attributionLogo:false },
    grid:{vertLines:{color:CHART_COLORS.grid},horzLines:{color:CHART_COLORS.grid}},
    rightPriceScale:{borderColor:'#d1d5db',scaleMargins:{top:.10,bottom:.10}},
    timeScale:{borderColor:'#d1d5db',timeVisible:false,secondsVisible:false,rightOffset:RIGHT_OFFSET,barSpacing:7,minBarSpacing:.2,fixRightEdge:false},
    crosshair:{mode:window.LightweightCharts.CrosshairMode.Normal,vertLine:{color:CHART_COLORS.cross,width:1,style:2},horzLine:{color:CHART_COLORS.cross,width:1,style:2}},
    handleScroll:{mouseWheel:false,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
    handleScale:{axisPressedMouseMove:true,mouseWheel:false,pinch:true},
    kineticScroll:{mouse:true,touch:true},
  };
}

function renderSeriesList() {
  const root = $('economic-series-list'); root.replaceChildren();
  const groups = new Map();
  SERIES.forEach(meta => { if(!groups.has(meta.category)) groups.set(meta.category,[]); groups.get(meta.category).push(meta); });
  for (const [category,metas] of groups) {
    const group = document.createElement('div'); group.className='economic-series-group';
    group.innerHTML=`<div class="economic-series-group-title">${category}</div>`;
    for (const meta of metas) {
      const button=document.createElement('button'); button.type='button'; button.className='economic-series-button'; button.dataset.seriesCode=meta.code;
      button.innerHTML=`${meta.title}<small>${frequencyLabel(meta.frequency)} · ${meta.unit}</small>`;
      button.addEventListener('click',()=>selectSeries(meta)); group.append(button);
    }
    root.append(group);
  }
}

function setActiveButton(code) {
  document.querySelectorAll('.economic-series-button').forEach(button=>button.classList.toggle('is-active',button.dataset.seriesCode===code));
}

function removeChart() {
  if (resizeObserver) { resizeObserver.disconnect(); resizeObserver=null; }
  if (chart) { chart.remove(); chart=null; }
  rawSeries=fastSeries=slowSeries=null; horizontalLines=[]; selectedLineId=null; lineMode=false;
  $('economic-line-tool').classList.remove('is-active'); $('economic-delete-line').disabled=true;
  $('economic-alert-layer').replaceChildren();
}

function fullRange() {
  if (!activeRows.length) return null;
  return {from:0,to:(activeRows.length-1)+RIGHT_OFFSET};
}

function fitMax() {
  const range=fullRange(); if(range&&chart) chart.timeScale().setVisibleLogicalRange(range);
}

function clampVisibleRange(range) {
  if (!chart || !activeRows.length || !range || applyingRange) return;
  const maxTo=(activeRows.length-1)+RIGHT_OFFSET;
  const full=fullRange();
  let from=range.from, to=range.to;
  const width=to-from;
  if (to>maxTo) { to=maxTo; from=to-width; }
  if (from<full.from && width>=full.to-full.from) { from=full.from; to=full.to; }
  if (Math.abs(from-range.from)>.01 || Math.abs(to-range.to)>.01) {
    applyingRange=true; chart.timeScale().setVisibleLogicalRange({from,to}); applyingRange=false;
  }
  updateBellPositions();
}

function handleWheel(event) {
  if (!chart || !activeRows.length) return;
  event.preventDefault();
  const range=chart.timeScale().getVisibleLogicalRange() || fullRange(); if(!range) return;
  const maxWidth=fullRange().to-fullRange().from;
  const width=Math.max(MIN_VISIBLE_BARS,Math.min(maxWidth,range.to-range.from));
  const factor=event.deltaY>0?1.16:.86;
  const nextWidth=Math.max(MIN_VISIBLE_BARS,Math.min(maxWidth,width*factor));
  const anchor=Math.min(range.to,(activeRows.length-1)+RIGHT_OFFSET);
  const next={from:anchor-nextWidth,to:anchor};
  if(next.from<0&&nextWidth>=maxWidth){next.from=0;next.to=fullRange().to;}
  applyingRange=true; chart.timeScale().setVisibleLogicalRange(next); applyingRange=false; updateBellPositions();
}

function findLine(id){return horizontalLines.find(line=>line.id===id);}
function lineStyle(line){return {price:line.price,color:line.id===selectedLineId?CHART_COLORS.selected:CHART_COLORS.line,lineWidth:line.id===selectedLineId?2:1,lineStyle:2,axisLabelVisible:true,title:formatValue(line.price)};}
function refreshLineStyles(){horizontalLines.forEach(line=>line.priceLine.applyOptions(lineStyle(line)));$('economic-delete-line').disabled=!selectedLineId;}
function selectLine(id){selectedLineId=id;refreshLineStyles();}

function addHorizontalLine(price, target=null) {
  if(!rawSeries||!Number.isFinite(price)) return null;
  const existing=target?horizontalLines.find(line=>line.target?.id===target.id):null;
  if(existing){existing.price=price;existing.priceLine.applyOptions({price});return existing;}
  const line={id:`line-${++lineCounter}`,price,target,priceLine:null};
  line.priceLine=rawSeries.createPriceLine(lineStyle(line)); horizontalLines.push(line); updateBellPositions(); return line;
}

function deleteSelectedLine() {
  const line=findLine(selectedLineId); if(!line||!rawSeries)return;
  rawSeries.removePriceLine(line.priceLine); horizontalLines=horizontalLines.filter(item=>item.id!==line.id); selectedLineId=null; refreshLineStyles(); updateBellPositions();
}

function clearLines() {
  if(!rawSeries)return; horizontalLines.forEach(line=>rawSeries.removePriceLine(line.priceLine)); horizontalLines=[];selectedLineId=null;refreshLineStyles();updateBellPositions();
}

function nearestLine(y) {
  let best=null,bestDistance=Infinity;
  for(const line of horizontalLines){const coordinate=rawSeries?.priceToCoordinate(line.price);if(coordinate==null)continue;const d=Math.abs(coordinate-y);if(d<bestDistance){best=line;bestDistance=d;}}
  return bestDistance<=7?best:null;
}

async function loadChartAlerts() {
  if(!sessionUser)return [];
  const {data,error}=await supabaseClient.from('targets').select('id,title,condition_type,target_value,last_value,is_active,source_config').eq('user_id',sessionUser.id).eq('source_type','economic_chart').eq('is_active',true);
  if(error)throw error;
  chartAlerts=(data||[]).filter(target=>target.source_config?.series_code);
  return chartAlerts;
}

function alertsForSeries(code){return chartAlerts.filter(target=>target.source_config?.series_code===code);}

function syncAlertLines() {
  if(!activeMeta||!rawSeries)return;
  for(const target of alertsForSeries(activeMeta.code)) addHorizontalLine(Number(target.target_value),target);
  updateBellPositions();
}

function updateBellPositions() {
  const layer=$('economic-alert-layer'); if(!layer||!rawSeries){return;} layer.replaceChildren();
  for(const line of horizontalLines){
    const y=rawSeries.priceToCoordinate(line.price); if(y==null||y<0||y>$('economic-chart-host').clientHeight)continue;
    const button=document.createElement('button');button.type='button';button.className=`economic-alert-bell${line.target?' is-active':''}`;button.style.top=`${y}px`;button.title=line.target?'알림 수정/삭제':'추적 알림 추가';button.textContent='🔔';
    button.addEventListener('click',event=>{event.stopPropagation();selectLine(line.id);openAlertModal(line);});layer.append(button);
  }
}

function closeAlertModal(){$('economic-alert-modal').hidden=true;$('economic-alert-form').dataset.lineId='';}
function openAlertModal(line){
  $('economic-alert-modal').hidden=false;$('economic-alert-form').dataset.lineId=line.id;$('economic-alert-title').textContent=line.target?'추적 알림 수정':'추적 알림 추가';$('economic-alert-series').textContent=activeMeta?.title||'';
  $('economic-alert-condition').value=line.target?.condition_type||'cross';$('economic-alert-value').value=Number(line.target?.target_value??line.price).toFixed(activeMeta?.decimals??2);$('economic-alert-delete').hidden=!line.target;
}

async function saveAlert(event){
  event.preventDefault();const line=findLine($('economic-alert-form').dataset.lineId);if(!line||!activeMeta||!sessionUser)return;
  const threshold=Number($('economic-alert-value').value);if(!Number.isFinite(threshold))return;
  const condition=$('economic-alert-condition').value;
  const payload={title:`${activeMeta.title} ${condition==='gte'?'상향':condition==='lte'?'하향':'상/하향'} 돌파`,condition_type:condition,target_value:threshold,last_value:activeRows.at(-1)?.value??null,last_checked_at:new Date().toISOString(),last_error:null,is_active:true,user_id:sessionUser.id,source_type:'economic_chart',source_config:{series_code:activeMeta.code,frequency:activeMeta.frequency}};
  if(line.target){const {data,error}=await supabaseClient.from('targets').update(payload).eq('id',line.target.id).eq('user_id',sessionUser.id).select().single();if(error)throw error;line.target=data;chartAlerts=chartAlerts.map(item=>item.id===data.id?data:item);}else{const {data,error}=await supabaseClient.from('targets').insert(payload).select().single();if(error)throw error;line.target=data;chartAlerts.push(data);}
  line.price=threshold;line.priceLine.applyOptions({price:threshold});closeAlertModal();refreshLineStyles();updateBellPositions();
}

async function deleteAlert(){
  const line=findLine($('economic-alert-form').dataset.lineId);if(!line?.target||!sessionUser)return;
  const targetId=line.target.id;const {error}=await supabaseClient.from('targets').delete().eq('id',targetId).eq('user_id',sessionUser.id);if(error)throw error;
  chartAlerts=chartAlerts.filter(item=>item.id!==targetId);line.target=null;closeAlertModal();updateBellPositions();
}

function renderChart(meta,rows){
  removeChart();activeRows=rows;const host=$('economic-chart-host');host.querySelector('.economic-empty')?.remove();
  if(!rows.length){host.insertAdjacentHTML('afterbegin',`<div class="economic-empty">${meta.pending||'저장된 데이터가 없습니다.'}</div>`);$('economic-note').textContent=meta.pending||'';return;}
  chart=window.LightweightCharts.createChart(host,chartOptions());
  const minMove=1/(10**meta.decimals),priceFormat={type:'price',precision:meta.decimals,minMove};
  rawSeries=chart.addLineSeries({color:CHART_COLORS.raw,lineWidth:2,priceFormat,lastValueVisible:true,priceLineVisible:true});rawSeries.setData(rows);
  const [fastWindow,slowWindow,fastLabel,slowLabel]=MA_WINDOWS[meta.frequency];
  fastSeries=chart.addLineSeries({color:CHART_COLORS.fast,lineWidth:1,priceFormat,lastValueVisible:false,priceLineVisible:false});
  slowSeries=chart.addLineSeries({color:CHART_COLORS.slow,lineWidth:1,priceFormat,lastValueVisible:false,priceLineVisible:false});
  fastSeries.setData(movingAverage(rows,fastWindow));slowSeries.setData(movingAverage(rows,slowWindow));
  $('economic-legend').children[1].textContent=`${fastLabel} 평균`;$('economic-legend').children[2].textContent=`${slowLabel} 평균`;
  $('economic-note').textContent=`최신 ${rows.at(-1).time} · ${formatValue(rows.at(-1).value,meta)} · 휠 확대/축소 · 드래그 이동`;
  fitMax();syncAlertLines();
  chart.subscribeClick(param=>{if(!param.point)return;if(lineMode){const p=rawSeries.coordinateToPrice(param.point.y);if(Number.isFinite(p)){const snapped=Math.round(p/minMove)*minMove;selectLine(addHorizontalLine(snapped).id);}lineMode=false;$('economic-line-tool').classList.remove('is-active');return;}const line=nearestLine(param.point.y);selectLine(line?.id||null);});
  chart.timeScale().subscribeVisibleLogicalRangeChange(clampVisibleRange);
  host.addEventListener('wheel',handleWheel,{passive:false});
  resizeObserver=new ResizeObserver(()=>{if(chart){chart.applyOptions({width:host.clientWidth,height:host.clientHeight});updateBellPositions();}});resizeObserver.observe(host);
}

async function selectSeries(meta){
  activeMeta=meta;setActiveButton(meta.code);$('economic-chart-title').textContent=meta.title;$('economic-chart-meta').textContent=`${frequencyLabel(meta.frequency)} · ${meta.unit}`;$('economic-status').textContent='불러오는 중';
  try{const rows=await fetchSeries(meta);renderChart(meta,rows);$('economic-status').textContent=rows.length?`${rows.length.toLocaleString()}개 관측값`:'저장 데이터 없음';}
  catch(error){removeChart();$('economic-chart-host').insertAdjacentHTML('afterbegin','<div class="economic-empty">데이터를 불러오지 못했습니다.</div>');$('economic-note').textContent=error?.message||'조회 오류';$('economic-status').textContent='조회 오류';}
}

async function initialize(){
  if(!supabaseClient||!window.LightweightCharts)return;
  const {data}=await supabaseClient.auth.getSession();if(!data.session){location.replace('index.html');return;}sessionUser=data.session.user;
  renderSeriesList();await loadChartAlerts();
  $('economic-line-tool').addEventListener('click',()=>{lineMode=!lineMode;$('economic-line-tool').classList.toggle('is-active',lineMode);});
  $('economic-delete-line').addEventListener('click',deleteSelectedLine);$('economic-clear-lines').addEventListener('click',clearLines);$('economic-fit-max').addEventListener('click',fitMax);
  document.querySelectorAll('[data-close-alert]').forEach(node=>node.addEventListener('click',closeAlertModal));$('economic-alert-form').addEventListener('submit',event=>saveAlert(event).catch(error=>alert(error.message)));$('economic-alert-delete').addEventListener('click',()=>deleteAlert().catch(error=>alert(error.message)));
  await selectSeries(SERIES.find(item=>item.code==='US10Y')||SERIES[0]);
}

document.addEventListener('DOMContentLoaded',initialize);
})();
