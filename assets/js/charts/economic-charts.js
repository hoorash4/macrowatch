(() => {
'use strict';

const cfg = window.MACROWATCH_CONFIG || {};
const supabaseClient = window.supabase?.createClient(cfg.supabaseUrl, cfg.supabasePublishableKey);
const CHART_COLORS = { raw:'#111827', fast:'#2563eb', slow:'#d97706', grid:'#e5e7eb', text:'#6b7280', cross:'#9ca3af', line:'#dc2626' };

const SERIES = [
  { code:'US2Y', title:'미국채 2년', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2 },
  { code:'US10Y', title:'미국채 10년', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2 },
  { code:'US10Y2Y', title:'미국 10Y-2Y 스프레드', frequency:'D', unit:'%p', category:'금리 · 신용', decimals:2 },
  { code:'HY_OAS', title:'미국 하이일드 OAS', frequency:'D', unit:'%p', category:'금리 · 신용', decimals:2 },
  { code:'EM_OAS', title:'이머징 채권 OAS', frequency:'D', unit:'%p', category:'금리 · 신용', decimals:2 },
  { code:'KR3Y', title:'국고채 3년', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2 },
  { code:'KR10Y', title:'국고채 10년', frequency:'D', unit:'%', category:'금리 · 신용', decimals:2 },
  { code:'KR10Y3Y', title:'국고채 10Y-3Y 스프레드', frequency:'D', unit:'%p', category:'금리 · 신용', decimals:2 },
  { code:'WTI', title:'WTI 유가', frequency:'D', unit:'USD', category:'시장가격', decimals:2 },
  { code:'USDKRW', title:'원/달러 환율', frequency:'D', unit:'원', category:'시장가격', decimals:1 },
  { code:'REDBOOK', title:'Redbook Index', frequency:'W', unit:'% YoY', category:'고빈도 경기', decimals:1, pending:'무료 공식 장기 API가 없어 자체 적재 경로를 준비 중입니다.' },
  { code:'WEI', title:'미국 Weekly Economic Index', frequency:'W', unit:'%', category:'고빈도 경기', decimals:2 },
  { code:'KR_EXPORT_DAILY_AVG', title:'한국 10일구간 일평균 수출', frequency:'T', unit:'억달러/조업일', category:'고빈도 경기', decimals:2, pending:'관세청 누계 수출액과 조업일수를 구간값으로 변환하는 수집기를 준비 중입니다.' },
  { code:'POLICY_EXPECTATION', title:'시장 내재 정책금리 기대', frequency:'D', unit:'bp', category:'MacroWatch 기존 데이터', decimals:1, adapter:'policy' },
  { code:'EM_CAPACITY', title:'이머징 자금 유입 여건', frequency:'D', unit:'지수', category:'MacroWatch 기존 데이터', decimals:2, adapter:'emCapacity' },
  { code:'US_SME_RISK', title:'미국 중소기업 위험지수', frequency:'M', unit:'지수', category:'MacroWatch 기존 데이터', decimals:2, adapter:'usSme' },
  { code:'KR_SME_RISK', title:'한국 중소기업 위험지수', frequency:'M', unit:'지수', category:'MacroWatch 기존 데이터', decimals:2, adapter:'krSme' },
  { code:'US_INFLATION', title:'미국 통합물가(YoY)', frequency:'M', unit:'%', category:'MacroWatch 기존 데이터', decimals:2, adapter:'inflation' },
];

const MA_WINDOWS = {
  D: [5, 20, '5일', '20일'],
  W: [4, 26, '4주', '26주'],
  T: [6, 18, '6구간', '18구간'],
  M: [6, 24, '6개월', '24개월'],
};

function movingAverage(rows, windowSize) {
  const result = [];
  let sum = 0;
  const queue = [];
  for (const row of rows) {
    queue.push(row.value);
    sum += row.value;
    if (queue.length > windowSize) sum -= queue.shift();
    if (queue.length === windowSize) result.push({ time: row.time, value: sum / windowSize });
  }
  return result;
}

async function pagedQuery(table, select, orderColumn, filters = []) {
  const pageSize = 1000;
  const rows = [];
  for (let from = 0; ; from += pageSize) {
    let query = supabaseClient.from(table).select(select).order(orderColumn, { ascending:true }).range(from, from + pageSize - 1);
    for (const filter of filters) query = query.eq(filter[0], filter[1]);
    const { data, error } = await query;
    if (error) throw error;
    rows.push(...(data || []));
    if (!data || data.length < pageSize) break;
  }
  return rows;
}

async function fetchSeries(meta) {
  let rows;
  if (!meta.adapter) {
    rows = await pagedQuery('economic_chart_points', 'observation_date,value', 'observation_date', [['series_code', meta.code]]);
    return rows.map(row => ({ time:String(row.observation_date).slice(0,10), value:Number(row.value) })).filter(row => Number.isFinite(row.value));
  }
  const adapters = {
    policy: ['policy_expectation_spreads', 'observation_date,expectation_spread_bps', 'observation_date', 'expectation_spread_bps'],
    emCapacity: ['em_capital_capacity_daily', 'observation_date,capacity_index', 'observation_date', 'capacity_index'],
    usSme: ['us_small_business_risk_monthly', 'month,risk_index', 'month', 'risk_index'],
    krSme: ['kr_small_business_risk_monthly', 'month,risk_index', 'month', 'risk_index'],
    inflation: ['us_inflation_monthly', 'month,headline_yoy_pct', 'month', 'headline_yoy_pct'],
  };
  const [table, select, dateKey, valueKey] = adapters[meta.adapter];
  rows = await pagedQuery(table, select, dateKey);
  return rows.map(row => ({ time:String(row[dateKey]).slice(0,10), value:Number(row[valueKey]) })).filter(row => Number.isFinite(row.value));
}

function formatValue(value, meta) {
  return `${Number(value).toFixed(meta.decimals)}${meta.unit ? ` ${meta.unit}` : ''}`;
}

function chartOptions() {
  return {
    layout:{ background:{ color:'#ffffff' }, textColor:CHART_COLORS.text, fontFamily:'Pretendard, system-ui, sans-serif', fontSize:11 },
    grid:{ vertLines:{ color:CHART_COLORS.grid }, horzLines:{ color:CHART_COLORS.grid } },
    rightPriceScale:{ borderColor:'#d1d5db', scaleMargins:{ top:0.12, bottom:0.12 } },
    timeScale:{ borderColor:'#d1d5db', timeVisible:false, secondsVisible:false, rightOffset:4, barSpacing:4, minBarSpacing:0.5 },
    crosshair:{ mode:window.LightweightCharts.CrosshairMode.Normal, vertLine:{ color:CHART_COLORS.cross, width:1, style:2 }, horzLine:{ color:CHART_COLORS.cross, width:1, style:2 } },
    handleScroll:{ mouseWheel:true, pressedMouseMove:true, horzTouchDrag:true, vertTouchDrag:false },
    handleScale:{ axisPressedMouseMove:true, mouseWheel:true, pinch:true },
    kineticScroll:{ mouse:true, touch:true },
  };
}

function buildCard(meta) {
  const card = document.createElement('article');
  card.className = 'economic-card';
  card.dataset.frequency = meta.frequency;
  card.innerHTML = `
    <header class="economic-card-header">
      <div><div class="economic-card-title">${meta.title}</div><div class="economic-card-meta">${meta.frequency === 'D' ? '일별' : meta.frequency === 'W' ? '주별' : meta.frequency === 'T' ? '10일 구간' : '월별'} · ${meta.unit}</div></div>
      <div class="economic-tools"><button type="button" data-line-tool>수평선</button><button type="button" data-clear-lines>선 지우기</button></div>
    </header>
    <div class="economic-chart" data-chart><div class="economic-empty">불러오는 중</div></div>
    <div class="economic-legend"><span>원값</span><span>단기평균</span><span>장기평균</span></div>
    <div class="economic-note" data-note>${meta.pending || '마우스 휠로 확대·축소, 드래그로 이동할 수 있습니다.'}</div>`;
  return card;
}

function renderChart(card, meta, rows) {
  const host = card.querySelector('[data-chart]');
  host.replaceChildren();
  if (!rows.length) {
    host.innerHTML = `<div class="economic-empty">${meta.pending || '저장된 데이터가 없습니다.'}</div>`;
    return;
  }
  const chart = window.LightweightCharts.createChart(host, chartOptions());
  const precision = meta.decimals;
  const minMove = 1 / (10 ** precision);
  const raw = chart.addLineSeries({ color:CHART_COLORS.raw, lineWidth:2, priceFormat:{ type:'price', precision, minMove } });
  raw.setData(rows);
  const [fastWindow, slowWindow, fastLabel, slowLabel] = MA_WINDOWS[meta.frequency];
  const fast = chart.addLineSeries({ color:CHART_COLORS.fast, lineWidth:1, priceFormat:{ type:'price', precision, minMove } });
  const slow = chart.addLineSeries({ color:CHART_COLORS.slow, lineWidth:1, priceFormat:{ type:'price', precision, minMove } });
  fast.setData(movingAverage(rows, fastWindow));
  slow.setData(movingAverage(rows, slowWindow));
  const legend = card.querySelector('.economic-legend');
  legend.children[1].textContent = `${fastLabel} 평균`;
  legend.children[2].textContent = `${slowLabel} 평균`;
  card.querySelector('[data-note]').textContent = `최신 ${rows[rows.length - 1].time} · ${formatValue(rows[rows.length - 1].value, meta)} · 휠 확대/축소 · 드래그 이동`;
  chart.timeScale().fitContent();

  const priceLines = [];
  let lineMode = false;
  const lineButton = card.querySelector('[data-line-tool]');
  lineButton.addEventListener('click', () => {
    lineMode = !lineMode;
    lineButton.classList.toggle('is-active', lineMode);
  });
  chart.subscribeClick((param) => {
    if (!lineMode || !param.point) return;
    const price = raw.coordinateToPrice(param.point.y);
    if (!Number.isFinite(price)) return;
    const snapped = Math.round(price / minMove) * minMove;
    priceLines.push(raw.createPriceLine({ price:snapped, color:CHART_COLORS.line, lineWidth:1, lineStyle:2, axisLabelVisible:true, title:formatValue(snapped, meta) }));
    lineMode = false;
    lineButton.classList.remove('is-active');
  });
  card.querySelector('[data-clear-lines]').addEventListener('click', () => {
    priceLines.splice(0).forEach(line => raw.removePriceLine(line));
  });
  new ResizeObserver(() => chart.applyOptions({ width:host.clientWidth, height:host.clientHeight })).observe(host);
}

async function initialize() {
  if (!supabaseClient || !window.LightweightCharts) return;
  const { data } = await supabaseClient.auth.getSession();
  if (!data.session) {
    location.replace('index.html');
    return;
  }
  const root = document.getElementById('economic-sections');
  const grouped = new Map();
  SERIES.forEach(meta => {
    if (!grouped.has(meta.category)) grouped.set(meta.category, []);
    grouped.get(meta.category).push(meta);
  });
  const cards = [];
  for (const [category, metas] of grouped) {
    const section = document.createElement('section');
    section.className = 'economic-section';
    section.innerHTML = `<h2 class="economic-section-title">${category}</h2><div class="economic-grid"></div>`;
    const grid = section.querySelector('.economic-grid');
    metas.forEach(meta => {
      const card = buildCard(meta);
      grid.append(card);
      cards.push([meta, card]);
    });
    root.append(section);
  }
  let failures = 0;
  await Promise.all(cards.map(async ([meta, card]) => {
    try {
      const rows = await fetchSeries(meta);
      renderChart(card, meta, rows);
    } catch (error) {
      failures += 1;
      card.querySelector('[data-chart]').innerHTML = '<div class="economic-empty">데이터를 불러오지 못했습니다.</div>';
      card.querySelector('[data-note]').textContent = error?.message || '조회 오류';
    }
  }));
  document.getElementById('economic-status').textContent = failures ? `${failures}개 차트 조회 오류` : '전체 차트 로드 완료';
}

document.addEventListener('DOMContentLoaded', initialize);
})();
