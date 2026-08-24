// ===== Supabase / API 연결 설정 =====
// 브라우저에서 Supabase 클라이언트를 만들 때 사용하는 공개 연결 정보입니다.
const { supabaseUrl: SUPABASE_URL, supabasePublishableKey: SUPABASE_KEY } = window.MACROWATCH_CONFIG;
const supabaseClient = window.macroWatchSupabase
|| (window.supabase ? window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY) : null);

// ===== 화면과 데이터의 현재 상태 =====
const ITEMS_PER_TRACK = 8;
const MAX_TRACKS = 10;
const MAX_TARGETS = ITEMS_PER_TRACK * MAX_TRACKS;

let targets = [];
let targetLoadError = false;
let currentEditId = null;
let currentDeleteId = null;
let draggedItemIndex = null;      // targets 배열의 전역 인덱스
let dropIndicatorIndex = null;    // targets 배열 기준 삽입 인덱스
let expandedTargetId = null;
let currentUserId = null;
let activeTrack = 1;
let suppressNextClick = false;
let suppressClickTimer = null;
let noticeCloseAction = null;
let pointerDragState = createPointerDragState();
let pendingToggleId = null;

const NEWS_SENTIMENT_HISTORY_DAYS = 60;
const CREDIT_STRESS_HISTORY_MONTHS = 60;
const NEWS_SENTIMENT_VIEWS = {
  recent: {
    days: 3,
    layout: 'horizontal',
    showNumbers: true,
    showDates: true,
  },
  expanded: {
    days: 30,
    layout: 'vertical',
    barWidthClass: 'min-w-6 max-w-6',
    gapClass: 'gap-0',
    showNumbers: true,
    showDates: true,
  },
  all: {
    days: 60,
    layout: 'vertical',
    barWidthClass: 'min-w-6 max-w-6',
    gapClass: 'gap-3',
    showNumbers: true,
    showDates: true,
  },
};
let newsSentimentRows = [];
let newsSentimentView = 'recent';

function formatNewsDate(value) {
  const [year, month, day] = String(value || '').split('-');
  return month && day ? `${Number(month)}/${Number(day)}` : '—';
}

function renderSentimentSegment(percent, colorClass, showLabel) {
  if (!percent) return '';
  const label = showLabel && percent >= 16 ? `<span class="text-[9px] font-bold text-white/90">${Math.round(percent)}%</span>` : '';
  return `<span class="flex items-center justify-center ${colorClass}" style="height:${percent}%">${label}</span>`;
}

function renderHorizontalSentimentSegment(percent, colorClass, showLabel) {
  if (!percent) return '';
  const label = showLabel && percent >= 12 ? `<span class="sentiment-segment-label text-[10px] font-bold">${Math.round(percent)}%</span>` : '';
  return `<span class="flex h-full items-center justify-center ${colorClass}" style="width:${percent}%">${label}</span>`;
}

function renderHorizontalSentimentBar(item, positive, negative, directionalCount, view, title) {
  const bar = directionalCount
    ? `${renderHorizontalSentimentSegment(positive, 'bg-red-900 transition group-hover:bg-red-800', view.showNumbers)}${renderHorizontalSentimentSegment(negative, 'bg-blue-900 transition group-hover:bg-blue-800', view.showNumbers)}`
    : '<span class="m-auto text-[9px] font-semibold text-slate-500">—</span>';
  const date = view.showDates ? `<span class="w-10 shrink-0 text-right text-xs font-semibold text-slate-600">${formatNewsDate(item.article_date)}</span>` : '';
  return `<div class="group flex w-full items-center gap-3"${title ? ` title="${title}"` : ''}>${date}<div class="flex h-12 min-w-0 flex-1 overflow-hidden rounded-lg bg-slate-200/80 ring-1 ring-inset ring-slate-300 shadow-sm">${bar}</div></div>`;
}

function renderVerticalSentimentBar(item, positive, negative, directionalCount, view, title) {
  const bar = directionalCount
    ? `${renderSentimentSegment(positive, 'bg-red-900 transition group-hover:bg-red-800', view.showNumbers)}${renderSentimentSegment(negative, 'bg-blue-900 transition group-hover:bg-blue-800', view.showNumbers)}`
    : '<span class="m-auto text-[9px] font-semibold text-slate-500">—</span>';
  const date = view.showDates ? `<span class="whitespace-nowrap text-[10px] text-slate-600">${formatNewsDate(item.article_date)}</span>` : '';
  return `<div class="group flex ${view.barWidthClass} flex-none flex-col items-center gap-2"${title ? ` title="${title}"` : ''}><div class="flex h-44 w-full flex-col overflow-hidden rounded-lg bg-slate-200/80 ring-1 ring-inset ring-slate-300 shadow-sm">${bar}</div>${date}</div>`;
}

function renderNewsSentiment(rows) {
  const chart = document.getElementById('news-sentiment-chart');
  if (!chart) return;

  const data = [...rows].sort((a, b) => String(a.article_date).localeCompare(String(b.article_date)));
  if (!data.length) {
    chart.innerHTML = '<div class="col-span-full flex min-h-40 items-center justify-center rounded-xl border border-dashed border-slate-800 bg-slate-950/30 p-5 text-sm text-slate-500">다음 뉴스 분석 후 최근 3일 추이가 표시됩니다.</div>';
    return;
  }

  const view = NEWS_SENTIMENT_VIEWS[newsSentimentView];
  const legend = '<div class="flex items-center gap-4 text-xs text-slate-400 sm:flex-col sm:items-start sm:justify-center sm:gap-3"><span class="inline-flex items-center gap-2"><i class="h-2 w-2 rounded-full bg-red-900"></i>긍정</span><span class="inline-flex items-center gap-2"><i class="h-2 w-2 rounded-full bg-blue-900"></i>부정</span></div>';
  const visibleRows = data.slice(-view.days);
  const displayRows = view.layout === 'horizontal' ? [...visibleRows].reverse() : visibleRows;
  const bars = displayRows.map((item) => {
    const directionalCount = Number(item.positive_count || 0) + Number(item.negative_count || 0);
    const positive = directionalCount ? (Number(item.positive_count || 0) / directionalCount) * 100 : 0;
    const negative = directionalCount ? (Number(item.negative_count || 0) / directionalCount) * 100 : 0;
    const title = view.showNumbers
      ? `${item.article_date}: 긍정 ${item.positive_count || 0}, 부정 ${item.negative_count || 0}${item.uncertain_count ? `, 판단 보류 ${item.uncertain_count}` : ''}`
      : `${item.article_date}: 긍정 ${Math.round(positive)}%, 부정 ${Math.round(negative)}%`;
    return view.layout === 'horizontal'
      ? renderHorizontalSentimentBar(item, positive, negative, directionalCount, view, title)
      : renderVerticalSentimentBar(item, positive, negative, directionalCount, view, title);
  }).join('');
  const controls = [
    newsSentimentView === 'recent' && data.length > NEWS_SENTIMENT_VIEWS.recent.days
      ? '<button type="button" data-news-sentiment-view="expanded" class="rounded-lg border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300 transition hover:border-slate-500 hover:text-white">더보기</button>'
      : '',
    newsSentimentView === 'expanded' && data.length > NEWS_SENTIMENT_VIEWS.expanded.days
      ? '<button type="button" data-news-sentiment-view="all" class="rounded-lg border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300 transition hover:border-slate-500 hover:text-white">이전 30일 더 보기</button>'
      : '',
    newsSentimentView !== 'recent'
      ? '<button type="button" data-news-sentiment-view="recent" class="rounded-lg border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300 transition hover:border-slate-500 hover:text-white">돌아가기</button>'
      : '',
  ].join('');
  const graphClass = view.layout === 'horizontal'
    ? 'flex h-60 min-w-0 flex-col justify-center gap-5 rounded-xl border border-slate-200 bg-slate-50 px-4 py-6'
    : `flex h-60 min-w-0 items-end justify-between ${view.gapClass} overflow-x-auto rounded-xl border border-slate-200 bg-slate-50 px-4 py-4`;
  const graphId = newsSentimentView === 'all' ? ' id="news-sentiment-history-scroll"' : '';
  chart.innerHTML = `${legend}<div${graphId} class="${graphClass}">${bars}</div>${controls ? `<div class="col-span-full flex justify-center gap-2">${controls}</div>` : ''}`;
  if (newsSentimentView === 'all') {
    const historyChart = document.getElementById('news-sentiment-history-scroll');
    if (historyChart) historyChart.scrollLeft = historyChart.scrollWidth;
  }
  chart.querySelectorAll('[data-news-sentiment-view]').forEach((button) => {
    button.addEventListener('click', () => {
      newsSentimentView = button.dataset.newsSentimentView;
      renderNewsSentiment(newsSentimentRows);
    });
  });
}

async function loadNewsSentimentDashboard() {
  const chart = document.getElementById('news-sentiment-chart');
  if (!chart || !supabaseClient) return;
  try {
    const { data, error } = await supabaseClient.from('news_daily_article_sentiment')
      .select('article_date,positive_count,negative_count,neutral_count,uncertain_count')
      .order('article_date', { ascending: false })
      .limit(NEWS_SENTIMENT_HISTORY_DAYS);
    if (error) throw error;
    newsSentimentRows = data || [];
    newsSentimentView = 'recent';
    renderNewsSentiment(newsSentimentRows);
  } catch (error) {
    chart.innerHTML = '<div class="col-span-full flex min-h-40 items-center justify-center rounded-xl border border-dashed border-slate-800 bg-slate-950/30 p-5 text-sm text-slate-500">잠시 후 다시 시도해 주세요.</div>';
  }
}

window.loadNewsSentimentDashboard = loadNewsSentimentDashboard;

function renderCreditStressDashboard(rows) {
  const chart = document.getElementById('credit-stress-chart');
  if (!chart) return;
  if (!rows.length) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">첫 산출 후 미국 시장 스트레스 지수가 표시됩니다.</div>';
    return;
  }

  const data = [...rows]
    .filter((row) => Number.isFinite(Number(row.stress_index)))
    .sort((a, b) => String(a.month).localeCompare(String(b.month)));
  if (!data.length) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">표시할 지수 데이터가 없습니다.</div>';
    return;
  }
  const width = 920;
  const height = 250;
  const padding = { top: 20, right: 24, bottom: 32, left: 52 };
  const scores = data.map((row) => Number(row.stress_index));
  const minimumScore = Math.min(...scores);
  const maximumScore = Math.max(...scores);
  const scoreRange = Math.max(maximumScore - minimumScore, Math.max(maximumScore * 0.1, 1));
  const gridStep = [0.5, 1, 2, 5, 10, 20, 50, 100].find((step) => step >= scoreRange / 4) || 100;
  const axisMinimum = Math.max(0, Math.floor((minimumScore - scoreRange * 0.15) / gridStep) * gridStep);
  const axisMaximum = Math.ceil((maximumScore + scoreRange * 0.15) / gridStep) * gridStep;
  const axisRange = axisMaximum - axisMinimum || gridStep;
  const x = (index) => padding.left + ((width - padding.left - padding.right) * index) / Math.max(1, data.length - 1);
  const y = (score) => padding.top + ((height - padding.top - padding.bottom) * (axisMaximum - score)) / axisRange;
  const labels = data.map((row, index) => {
    const month = String(row.month || '');
    if (!month.endsWith('-01') || (index !== 0 && !month.endsWith('-01-01'))) return '';
    return `<text x="${x(index)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${month.slice(0, 4)}</text>`;
  }).join('');
  const lines = data.slice(1).map((row, index) => {
    const previous = data[index];
    const provisional = row.is_provisional || previous.is_provisional;
    return `<line x1="${x(index)}" y1="${y(Number(previous.stress_index))}" x2="${x(index + 1)}" y2="${y(Number(row.stress_index))}" stroke="#b7791f" stroke-width="2.75" stroke-linecap="round"${provisional ? ' stroke-dasharray="5 5"' : ''}/>`;
  }).join('');
  const dots = data.map((row, index) => {
    const provisional = Boolean(row.is_provisional);
    const detail = `${row.month}\nUS-MSI: ${Number(row.stress_index).toFixed(1)}${provisional ? ' (잠정치)' : ' (확정치)'}`;
    return `<circle cx="${x(index)}" cy="${y(Number(row.stress_index))}" r="3.75" fill="#b7791f"${provisional ? ' fill-opacity="0.35" stroke="#b7791f" stroke-width="1.5"' : ''} tabindex="0"><title>${detail}</title></circle>`;
  }).join('');
  const grid = Array.from({ length: Math.round(axisRange / gridStep) + 1 }, (_, index) => axisMinimum + index * gridStep)
    .map((score) => `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(score)}" y2="${y(score)}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text x="${padding.left - 9}" y="${y(score) + 3}" text-anchor="end" fill="#64748b" font-size="10">${Number.isInteger(score) ? score : score.toFixed(1)}</text>`).join('');
  chart.innerHTML = `<div class="mb-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-400"><span class="inline-flex items-center gap-2"><i class="h-0.5 w-5 bg-amber-600"></i>확정치</span><span class="inline-flex items-center gap-2"><i class="h-0.5 w-5 border-t-2 border-dashed border-amber-600"></i>잠정치</span></div><div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><svg class="h-60 w-full" viewBox="0 0 ${width} ${height}" role="img" aria-label="미국 시장 스트레스 지수 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${lines}${dots}${labels}</svg></div><p class="mt-3 text-[11px] text-slate-500">높을수록 시장 스트레스가 높음을 뜻합니다. 점선 구간은 잠정치이며, 지수를 구성하는 전체 지표가 업데이트되면 확정값으로 전환됩니다.</p>`;
}

async function loadCreditStressDashboard() {
  const chart = document.getElementById('credit-stress-chart');
  if (!chart || !supabaseClient) return;
  try {
    const { data, error } = await supabaseClient.from('us_market_stress_index_monthly')
      .select('month,stress_index,is_provisional')
      .order('month', { ascending: false })
      .limit(CREDIT_STRESS_HISTORY_MONTHS);
    if (error) throw error;
    renderCreditStressDashboard(data || []);
  } catch (error) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">시장 스트레스 지수를 불러오지 못했습니다.</div>';
  }
}

window.loadCreditStressDashboard = loadCreditStressDashboard;

function toCreditStressNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function buildCreditStressScores(rows, key) {
  const available = (value) => Number.isFinite(value) && (key !== 'business_bankruptcy_filings' || value > 0);
  const values = rows.map((row) => toCreditStressNumber(row[key])).filter(available).sort((a, b) => a - b);
  if (!values.length) return new Map();
  const lower = values[Math.floor((values.length - 1) * 0.05)];
  const upper = values[Math.ceil((values.length - 1) * 0.95)];
  const spread = upper - lower || 1;
  return new Map(rows.map((row) => {
    const value = toCreditStressNumber(row[key]);
    return [row.month, available(value) ? Math.max(0, Math.min(100, ((value - lower) / spread) * 100)) : null];
  }));
}

function renderCreditStressComponents(rows) {
  const chart = document.getElementById('credit-stress-components-chart');
  if (!chart) return;
  if (!rows.length) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">첫 수집 후 장기 신용위험 추이가 표시됩니다.</div>';
    return;
  }
  const data = [...rows].sort((a, b) => String(a.month).localeCompare(String(b.month)));
  const series = [
    { key: 'high_yield_oas_pct', label: '하이일드 스프레드', color: '#9f3030', digits: 2, suffix: '%p' },
    { key: 'financial_conditions_credit_index', label: '금융 신용여건', color: '#b7791f', digits: 3, suffix: '' },
    { key: 'business_bankruptcy_filings', label: '기업 파산보호 신청', color: '#285e8e', digits: 0, suffix: '건' },
  ].map((item) => ({ ...item, scores: buildCreditStressScores(data, item.key) }));
  const width = 920;
  const height = 250;
  const padding = { top: 20, right: 24, bottom: 32, left: 24 };
  const x = (index) => padding.left + ((width - padding.left - padding.right) * index) / Math.max(1, data.length - 1);
  const y = (score) => padding.top + ((height - padding.top - padding.bottom) * (100 - score)) / 100;
  const pathFor = (item) => {
    let path = '';
    let connected = false;
    data.forEach((row, index) => {
      const score = item.scores.get(row.month);
      if (score == null) { connected = false; return; }
      path += `${connected ? 'L' : 'M'}${x(index).toFixed(1)},${y(score).toFixed(1)}`;
      connected = true;
    });
    return path;
  };
  const labels = data.map((row, index) => String(row.month || '').endsWith('-01') && (index === 0 || String(row.month).endsWith('-01-01')) ? `<text x="${x(index)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${String(row.month).slice(0, 4)}</text>` : '').join('');
  const dots = series.flatMap((item) => data.map((row, index) => {
    const score = item.scores.get(row.month);
    if (score == null) return '';
    const value = toCreditStressNumber(row[item.key]);
    const detail = `${row.month}\n${item.label}: ${value.toFixed(item.digits)}${item.suffix}`;
    return `<circle cx="${x(index)}" cy="${y(score)}" r="3.5" fill="${item.color}" tabindex="0"><title>${detail}</title></circle>`;
  })).join('');
  const legend = series.map((item) => `<span class="inline-flex items-center gap-2"><i class="h-2.5 w-2.5 rounded-full" style="background:${item.color}"></i>${item.label}</span>`).join('');
  chart.innerHTML = `<div class="mb-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-400">${legend}</div><div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><svg class="h-60 w-full" viewBox="0 0 ${width} ${height}" role="img" aria-label="미국 신용 위험 장기 추이">${[25, 50, 75].map((score) => `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(score)}" y2="${y(score)}" stroke="#dbe3ed" stroke-dasharray="3 4"/>`).join('')}${series.map((item) => `<path d="${pathFor(item)}" fill="none" stroke="${item.color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`).join('')}${dots}${labels}</svg></div><p class="mt-3 text-[11px] text-slate-500">서로 단위가 다른 세 지표의 추세를 함께 비교합니다. 점에 마우스를 올리면 원 수치를 확인할 수 있습니다.</p>`;
}

async function loadCreditStressComponentsDashboard() {
  const chart = document.getElementById('credit-stress-components-chart');
  if (!chart || !supabaseClient) return;
  try {
    const { data, error } = await supabaseClient.from('us_credit_stress_monthly')
      .select('month,high_yield_oas_pct,financial_conditions_credit_index,business_bankruptcy_filings')
      .order('month', { ascending: false })
      .limit(CREDIT_STRESS_HISTORY_MONTHS);
    if (error) throw error;
    renderCreditStressComponents(data || []);
  } catch (error) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">신용위험 데이터를 불러오지 못했습니다.</div>';
  }
}

window.loadCreditStressComponentsDashboard = loadCreditStressComponentsDashboard;

// 알림 조건에 따라 설정값 입력칸을 활성화하거나 비활성화합니다.
function toggleTargetValueInput(conditionId, valueId) {
  const condition = document.getElementById(conditionId);
  const valueInput = document.getElementById(valueId);
  if (!condition || !valueInput) return;
  const isChanged = condition.value === 'changed';
  valueInput.disabled = isChanged;
  valueInput.placeholder = isChanged
    ? '예: 설정 없이 지표의 값이 변동하면 알려드립니다.'
    : '예: 4.50';
  valueInput.classList.toggle('opacity-50', isChanged);
}

function initializeDashboardNavigation() {
  const buttons = [...document.querySelectorAll('[data-dashboard-view]')];
  const panels = [...document.querySelectorAll('[data-dashboard-panel]')];
  if (!buttons.length || !panels.length) return;

  const hashByView = { overview: '#news', credit: '#credit' };
  const viewByHash = {
    ...Object.fromEntries(Object.entries(hashByView).map(([view, hash]) => [hash, view])),
    '#tracker': 'overview',
  };

  const selectView = (view, updateHash = false) => {
    const selectedView = hashByView[view] ? view : 'overview';
    buttons.forEach((button) => {
      const active = button.dataset.dashboardView === selectedView;
      button.classList.toggle('is-active', active);
      button.toggleAttribute('aria-current', active);
    });
    panels.forEach((panel) => panel.classList.toggle('dashboard-panel-hidden', panel.dataset.dashboardPanel !== selectedView));
    if (updateHash && location.hash !== hashByView[selectedView]) location.hash = hashByView[selectedView];
  };

  buttons.forEach((button) => button.addEventListener('click', () => selectView(button.dataset.dashboardView, true)));
  window.addEventListener('hashchange', () => selectView(viewByHash[location.hash]));
  selectView(viewByHash[location.hash] || buttons.find((button) => button.classList.contains('is-active'))?.dataset.dashboardView || buttons[0].dataset.dashboardView);
}

function initializeDashboardScrollState() {
  const navigation = document.querySelector('.dashboard-nav');
  if (!navigation) return;

  const updateScrollState = () => {
    document.body.classList.toggle('dashboard-is-scrolled', navigation.getBoundingClientRect().top <= 0);
  };

  window.addEventListener('scroll', updateScrollState, { passive: true });
  updateScrollState();
}

// ===== 페이지 초기화 =====
// 페이지의 HTML이 모두 만들어진 뒤 한 번만 실행되는 초기 설정입니다.
// 입력폼 초기 상태, 안내 모달 닫기 버튼, Drag & Drop 도움말을 여기서 연결합니다.
document.addEventListener('DOMContentLoaded', () => {
  initializeDashboardNavigation();
  initializeDashboardScrollState();

  // 신규 등록폼은 공식 API 직접 입력으로 시작합니다.
  toggleTypeFields();

  // 알림 조건이 '변동 감지'인지 확인해서 설정값 입력칸 활성/비활성 상태를 맞춥니다.
  toggleTargetValueInput('input-condition', 'input-target-val');

  // '서비스 준비 중 / 등록 되었습니다' 공용 안내창의 확인 버튼입니다.
  const preparingClose = document.getElementById('service-preparing-close');
  if (preparingClose) {
    preparingClose.addEventListener('click', closeServicePreparingModal);
  }

  // 마우스와 터치가 같은 Pointer Events 드래그 흐름을 사용합니다.
  const targetList = document.getElementById('target-list');
  targetList?.addEventListener('pointerdown', handlePointerDragStart);
  window.addEventListener('resize', updateTrackTabSizing);
  document.addEventListener('pointermove', handlePointerDragMove, { passive: false });
  document.addEventListener('pointerup', handlePointerDragEnd);
  document.addEventListener('pointercancel', handlePointerDragCancel);

  // 드래그 직후 발생하는 클릭은 한 번만 차단합니다.
  document.addEventListener('click', (event) => {
    if (!suppressNextClick) return;

    suppressNextClick = false;
    if (suppressClickTimer) {
      window.clearTimeout(suppressClickTimer);
      suppressClickTimer = null;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  // 화면의 다른 곳을 누르면 열린 도움말을 자동으로 닫습니다.
  document.addEventListener('click', (event) => {
    const helpBox = event.target.closest('.track-help');
    const isTouchLayout = !window.matchMedia('(hover: hover) and (pointer: fine)').matches;

    if (helpBox && isTouchLayout) {
      event.stopPropagation();

      const willOpen = !helpBox.classList.contains('is-open');
      document.querySelector('.track-help.is-open')?.classList.remove('is-open');

      helpBox.classList.toggle('is-open', willOpen);
      return;
    }

    const openedHelp = document.querySelector('.track-help.is-open');
    if (openedHelp) {
      openedHelp.classList.remove('is-open');
    }
  });
});

// 드래그가 끝난 직후 생성되는 클릭만 한 번 막습니다.
// 탭 이동으로 대상 DOM이 다시 그려져 클릭 자체가 사라진 경우에는
// 다음 이벤트 턴에 자동 해제해, 사용자의 다음 탭 클릭을 막지 않습니다.
function suppressClickAfterDrag() {
  suppressNextClick = true;

  if (suppressClickTimer) window.clearTimeout(suppressClickTimer);
  suppressClickTimer = window.setTimeout(() => {
    suppressNextClick = false;
    suppressClickTimer = null;
  }, 0);
}

// 현재 로그인한 사용자의 ID를 가져옵니다.
// 이미 한 번 확인한 ID가 있으면 다시 서버에 묻지 않고 저장된 값을 사용합니다.
async function getCurrentUserId() {
  if (currentUserId) return currentUserId;
  const { data } = await supabaseClient.auth.getSession();
  currentUserId = data.session?.user?.id || null;
  return currentUserId;
}

async function requireCurrentUserId() {
  const userId = await getCurrentUserId();
  if (userId) return userId;
  showCenteredNotice(
    '로그인이 필요합니다.',
    '확인을 누르면 로그인 화면으로 돌아갑니다.',
  );
  return null;
}

// ===== DB 연결 상태 표시 =====
function setDbStatus(state) {
  const statusEl = document.getElementById('db-status');
  if (!statusEl) return;
  const states = {
    loading: ['bg-slate-900/60 text-slate-400 border-slate-800', 'bg-slate-500 animate-pulse', 'DB 연결 확인 중...'],
    connected: ['bg-green-950/60 text-green-400 border-green-700/50', 'bg-green-400', 'DB 연결 완료'],
    missing: ['bg-red-950/60 text-red-400 border-red-700/50', 'bg-red-400', 'DB 설정 필요'],
    error: ['bg-amber-950/60 text-amber-400 border-amber-700/50', 'bg-amber-400', 'DB 연결 오류']
  };
  const [colors, dot, label] = states[state] || states.error;
  statusEl.className = `px-3 py-1.5 rounded-full text-xs font-semibold border flex items-center gap-2 shadow-inner ${colors}`;
  statusEl.innerHTML = `<span class="w-2 h-2 rounded-full ${dot}"></span> ${label}`;
}


// 사용자가 선택한 공식 API에 맞는 입력칸만 보여줍니다.
function toggleTypeFields() {
  const typeInput = document.getElementById('input-type');
  if (!typeInput) return;
  const selectedType = typeInput.value;
  document.getElementById('field-fred')?.classList.toggle('hidden', selectedType !== 'FRED');
  document.getElementById('field-bok')?.classList.toggle('hidden', selectedType !== 'BOK');
}

// 선택한 공식 API에 맞는 입력칸만 표시합니다.
function handleSourceTypeChange() {
  toggleTypeFields();
}

// ===== 공식 API 지표 후보 검색 =====
// 한국어 검색어는 Edge Function 안의 경제지표 용어 사전으로 FRED 검색어 후보를 만들고,
// ECOS는 한국어 통계표 후보를 바로 반환합니다. 선택 결과만 기존 입력폼에 반영합니다.
let indicatorSearchResults = [];
let indicatorSearchQuery = '';
let indicatorSearchSource = '';

function getIndicatorSearchResultKey(result) {
  return [result.source, result.kind, result.code, result.itemCode || ''].join(':');
}

function closeIndicatorSearchModal() {
  document.getElementById('indicator-search-modal')?.classList.add('hidden');
}

function getIndicatorSearchSource() {
  return document.getElementById('input-type')?.value === 'BOK' ? 'BOK' : 'FRED';
}

function getIndicatorSearchSourceLabel(source) {
  return source === 'BOK' ? '한국은행 ECOS API' : '연준 FRED API';
}

function renderIndicatorSearchResults(results, warning = '', append = false, allowMore = true) {
  const container = document.getElementById('indicator-search-results');
  const modal = document.getElementById('indicator-search-modal');
  const description = document.getElementById('indicator-search-description');
  if (!container || !modal) return;

  if (!append) {
    indicatorSearchResults = [];
    container.replaceChildren();
  }

  container.querySelector('#indicator-search-more')?.remove();
  const known = new Set(indicatorSearchResults.map(getIndicatorSearchResultKey));
  const additions = results.filter((result) => !known.has(getIndicatorSearchResultKey(result)));
  const startIndex = indicatorSearchResults.length;
  indicatorSearchResults.push(...additions);

  if (description) {
    description.textContent = allowMore
      ? `${getIndicatorSearchSourceLabel(indicatorSearchSource)}에서 찾은 후보입니다. 원하는 항목을 선택하면 공식 코드가 자동으로 채워집니다.`
      : '원하는 통계 항목을 선택하면 공식 코드가 자동으로 채워집니다.';
  }

  if (warning) {
    const warningBox = document.createElement('p');
    warningBox.className = 'mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-200';
    warningBox.textContent = warning;
    container.append(warningBox);
  }

  if (!additions.length) {
    const empty = document.createElement('p');
    empty.className = 'rounded-xl border border-slate-700 bg-slate-950/50 p-5 text-center text-sm text-slate-400';
    empty.textContent = append
      ? '추가 후보가 없습니다.'
      : indicatorSearchSource === 'BOK'
        ? '선택한 ECOS에서 일치하는 후보를 찾지 못했습니다.'
        : '선택한 FRED에서 일치하는 후보를 찾지 못했습니다. 영어 검색어로도 시도해 보세요.';
    container.append(empty);
  } else {
    additions.forEach((result, offset) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'mb-2 w-full rounded-xl border border-slate-700 bg-slate-950/40 p-3 text-left transition hover:border-amber-400 hover:bg-slate-800';
      button.addEventListener('click', () => selectIndicatorSearchResult(startIndex + offset));

      const source = document.createElement('span');
      source.className = result.source === 'FRED'
        ? 'mr-2 rounded-full bg-blue-500/15 px-2 py-1 text-[10px] font-bold text-blue-300'
        : 'mr-2 rounded-full bg-emerald-500/15 px-2 py-1 text-[10px] font-bold text-emerald-300';
      source.textContent = result.source;

      const title = document.createElement('strong');
      title.className = 'text-sm text-white';
      title.textContent = result.title;

      const meta = document.createElement('p');
      meta.className = 'mt-2 text-xs text-slate-400';
      meta.textContent = result.kind === 'table'
        ? `통계표 코드: ${result.code} · 항목을 선택하려면 누르세요.`
        : `코드: ${result.code}${result.itemCode ? ` · 항목: ${result.itemCode}` : ''}${result.frequency ? ` · ${result.frequency}` : ''}${result.unit ? ` · ${result.unit}` : ''}`;

      button.append(source, title, meta);
      container.append(button);
    });
  }

  if (allowMore && additions.length) {
    const more = document.createElement('button');
    more.id = 'indicator-search-more';
    more.type = 'button';
    more.className = 'mt-2 w-full rounded-lg border border-slate-600 bg-slate-800 px-4 py-2.5 text-sm font-bold text-slate-200 transition hover:border-amber-400 hover:text-white';
    more.textContent = '다른 후보 더 보기';
    more.addEventListener('click', () => searchIndicators(true));
    container.append(more);
  }

  modal.classList.remove('hidden');
}

function buildFredSearchTerms(query) {
  const normalizedQuery = String(query || '').trim().replace(/\s+/g, ' ').toLowerCase();
  const terms = Array.isArray(window.MACROWATCH_FRED_TERMS) ? window.MACROWATCH_FRED_TERMS : [];
  const candidates = [];

  terms
    .slice()
    .sort((left, right) => Math.max(...right.terms.map((term) => term.length)) - Math.max(...left.terms.map((term) => term.length)))
    .forEach((entry) => {
      if (entry.terms.some((term) => normalizedQuery.includes(String(term).toLowerCase()))) {
        candidates.push(...entry.queries);
      }
    });

  if (/[a-z]/i.test(query)) candidates.unshift(query.trim());
  return [...new Set(candidates)].slice(0, 4);
}

function handleIndicatorSearchKeydown(event) {
  if (event.key !== 'Enter' || event.isComposing) return;
  event.preventDefault();
  searchIndicators();
}

async function searchIndicators(loadMore = false) {
  const input = document.getElementById('indicator-search-query');
  const mainButton = document.getElementById('indicator-search-button');
  const moreButton = document.getElementById('indicator-search-more');
  const query = loadMore ? indicatorSearchQuery : (input?.value.trim() || '');
  const source = loadMore ? indicatorSearchSource : getIndicatorSearchSource();

  if (query.length < 2) {
    showCenteredNotice('검색어를 입력해 주세요.', '두 글자 이상 입력하면 선택한 공식 API에서 지표 후보를 찾습니다.');
    return;
  }

  if (!supabaseClient) {
    showCenteredNotice('검색을 사용할 수 없습니다.', 'Supabase 연결 정보를 확인해 주세요.');
    return;
  }

  const button = loadMore ? moreButton : mainButton;
  if (button) {
    button.disabled = true;
    button.textContent = '찾는 중';
  }

  try {
    const { data } = await supabaseClient.auth.getSession();
    const token = data.session?.access_token;
    if (!token) throw new Error('로그인이 필요합니다.');

    if (!loadMore) {
      indicatorSearchQuery = query;
      indicatorSearchSource = source;
    }

    const response = await fetch(SUPABASE_URL + '/functions/v1/search-indicators', {
      method: 'POST',
      headers: {
        apikey: SUPABASE_KEY,
        Authorization: 'Bearer ' + token,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        action: 'search',
        source,
        query,
        fredQueries: source === 'FRED' ? buildFredSearchTerms(query) : [],
        excludedCodes: indicatorSearchResults.map((result) => result.code),
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || '지표 후보를 불러오지 못했습니다.');

    renderIndicatorSearchResults(payload.results || [], payload.warning || '', loadMore);
  } catch (error) {
    const message = error?.message || '지표 후보를 불러오지 못했습니다.';
    if (message.includes('로그인이 필요합니다')) {
      showCenteredNotice('로그인이 필요합니다.', '확인을 누르면 로그인 화면으로 돌아갑니다.');
    } else {
      showCenteredNotice('지표 후보 검색 실패', message);
    }
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = loadMore ? '다른 후보 더 보기' : '찾기';
    }
  }
}

function fillIndicatorTitleIfEmpty(title) {
  const input = document.getElementById('input-title');
  if (input && !input.value.trim()) input.value = title || '';
}

async function selectIndicatorSearchResult(index) {
  const result = indicatorSearchResults[index];
  if (!result) return;

  if (result.source === 'ECOS' && result.kind === 'table') {
    // ECOS의 첫 후보는 통계표입니다. 통계표 코드를 먼저 채워 두고 세부 항목을 선택합니다.
    fillIndicatorTitleIfEmpty(result.title);
    document.getElementById('input-type').value = 'BOK';
    toggleTypeFields();
    document.getElementById('input-bok-code').value = result.code || '';
    document.getElementById('input-bok-item-code').value = '';
    if (result.frequency) document.getElementById('input-bok-cycle').value = result.frequency;

    const container = document.getElementById('indicator-search-results');
    if (container) container.innerHTML = '<p class="p-5 text-center text-sm text-slate-400">ECOS 통계 항목을 불러오는 중입니다.</p>';

    try {
      const { data } = await supabaseClient.auth.getSession();
      const token = data.session?.access_token;
      if (!token) throw new Error('로그인이 필요합니다.');

      const response = await fetch(SUPABASE_URL + '/functions/v1/search-indicators', {
        method: 'POST',
        headers: {
          apikey: SUPABASE_KEY,
          Authorization: 'Bearer ' + token,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ action: 'ecos-items', statCode: result.code, tableTitle: result.title }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || 'ECOS 통계 항목을 불러오지 못했습니다.');

      const items = Array.isArray(payload.results) ? payload.results : [];
      if (items.length) {
        renderIndicatorSearchResults(items, '', false, false);
        const description = document.getElementById('indicator-search-description');
        if (description) description.textContent = '통계표 코드를 입력했습니다. 등록할 세부 항목을 하나 선택해 항목 코드까지 채워 주세요.';
      } else {
        renderIndicatorSearchResults([{
          source: 'ECOS',
          kind: 'series',
          title: result.title,
          code: result.code,
          itemCode: '',
          frequency: result.frequency || '',
          unit: '',
        }], '', false, false);
        const description = document.getElementById('indicator-search-description');
        if (description) description.textContent = '이 통계표에는 별도 세부 항목이 없습니다. 이 항목을 선택하면 통계표 코드만으로 등록합니다.';
      }
    } catch (error) {
      closeIndicatorSearchModal();
      showCenteredNotice('ECOS 항목 검색 실패', error?.message || '통계 항목을 불러오지 못했습니다.');
    }
    return;
  }

  fillIndicatorTitleIfEmpty(result.title);
  document.getElementById('input-type').value = result.source === 'FRED' ? 'FRED' : 'BOK';
  toggleTypeFields();

  if (result.source === 'FRED') {
    document.getElementById('input-fred-id').value = result.code || '';
  } else {
    document.getElementById('input-bok-code').value = result.code || '';
    document.getElementById('input-bok-item-code').value = result.itemCode || '';
    if (result.frequency) document.getElementById('input-bok-cycle').value = result.frequency;
  }

  closeIndicatorSearchModal();
}

// ===== 추적 지표 목록 불러오기 =====
async function fetchTargets() {
  if (!supabaseClient) {
    setDbStatus('missing');
    targets = [];
    renderTargets();
    return;
  }
  setDbStatus('loading');
  targetLoadError = false;
  try {
    const userId = await getCurrentUserId();
    if (!userId) throw new Error('로그인 정보를 확인하지 못했습니다.');
    const { data, error } = await supabaseClient
    .from('targets')
    .select('*')
    .eq('user_id', userId)
    .order('display_order', { ascending: true, nullsFirst: false });
    if (error) throw error;
    targets = data || [];
    setDbStatus('connected');
  } catch (error) {
    console.error('Target fetch error:', error);
    targets = [];
    targetLoadError = true;
    setDbStatus('error');
  }
  renderTargets();
}

// ===== Track 계산 / Track 탭 표시 =====
// 현재 지표 개수로 필요한 Track 개수를 계산합니다.
function getTrackCount() {
  return Math.max(1, Math.min(MAX_TRACKS, Math.ceil(targets.length / ITEMS_PER_TRACK)));
}

// 특정 Track이 targets 배열에서 시작하는 위치를 계산합니다.
function getTrackStartIndex(trackNumber = activeTrack) {
  return (trackNumber - 1) * ITEMS_PER_TRACK;
}

// 특정 Track이 targets 배열에서 끝나는 위치를 계산합니다.
// 마지막 Track에 8개가 꽉 차지 않아도 실제 데이터 개수까지만 반환합니다.
function getTrackEndIndex(trackNumber = activeTrack) {
  return Math.min(getTrackStartIndex(trackNumber) + ITEMS_PER_TRACK, targets.length);
}

// 현재 선택된 Track 번호가 실제 존재하는 범위를 벗어나지 않게 보정합니다.
// 지표 삭제로 Track 수가 줄었을 때 잘못된 Track 번호가 남는 것을 막습니다.
function normalizeActiveTrack() {
  const trackCount = getTrackCount();
  if (activeTrack > trackCount) activeTrack = trackCount;
  if (activeTrack < 1) activeTrack = 1;
}

// 사용자가 Track 탭을 눌렀을 때 해당 Track으로 화면을 전환합니다.
// 펼쳐 둔 상세정보와 드롭 위치 표시는 초기화합니다.
function switchTrack(trackNumber) {
  const trackCount = getTrackCount();
  if (trackNumber < 1 || trackNumber > trackCount || trackNumber === activeTrack) return;

  activeTrack = trackNumber;
  expandedTargetId = null;
  clearDropIndicator();
  renderTargets();
}

// 현재 지표 개수에 맞춰 Track 01~10 탭과 + ADD Track 탭을 새로 그립니다.
// 활성 Track에는 is-active 클래스를 붙이고, 각 탭에 클릭/드롭 이벤트를 연결합니다.
function renderTrackTabs() {
  const tabsEl = document.querySelector('#tab-content-tracker > .sheet-tabs');
  if (!tabsEl) return;

  normalizeActiveTrack();
  const trackCount = getTrackCount();

  tabsEl.innerHTML = '';

  for (let trackNumber = 1; trackNumber <= trackCount; trackNumber += 1) {
    const button = document.createElement('button');
    const isActive = trackNumber === activeTrack;

    button.type = 'button';
    button.className = `sheet-tab${isActive ? ' is-active' : ''}`;
    button.style.zIndex = String(20 - Math.abs(trackNumber - activeTrack));
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-selected', String(isActive));
    button.dataset.track = String(trackNumber);
    const tabNumber = String(trackNumber).padStart(2, '0');
    button.innerHTML = `
      <span class="tab-label-full">Tab ${tabNumber}</span>
      <span class="tab-label-short">T${tabNumber}</span>
    `;

    button.addEventListener('click', () => switchTrack(trackNumber));

    tabsEl.appendChild(button);
  }

  if (trackCount < MAX_TRACKS) {
    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'sheet-tab sheet-tab-add';
    addButton.setAttribute('role', 'button');
    addButton.innerHTML = `
      <span class="tab-label-full">+ Tab</span>
      <span class="tab-label-short">+</span>
    `;
    addButton.addEventListener('click', showAddTrackNotice);
    tabsEl.appendChild(addButton);
  }

  updateTrackTabSizing();
  requestAnimationFrame(updateTrackTabSizing);
}

// 탭은 기본 폭(96px)을 유지하다가, 오른쪽 라운딩 전 사용 가능한 폭에 닿으면
// 모든 탭이 같은 비율로 줄어듭니다. 'Tab 01'이 답답해지기 전에는 'T01'으로 전환합니다.
function updateTrackTabSizing() {
  const tabsEl = document.querySelector('#tab-content-tracker > .sheet-tabs');
  if (!tabsEl) return;

  const tabCount = tabsEl.querySelectorAll('.sheet-tab').length;
  if (tabCount === 0 || tabsEl.clientWidth === 0) return;

  const styles = getComputedStyle(tabsEl);
  const horizontalPadding = parseFloat(styles.paddingLeft)
    + parseFloat(styles.paddingRight);
  const availableWidth = tabsEl.clientWidth - horizontalPadding;
  const overlapWidth = 10 * Math.max(0, tabCount - 1);
  const fittedWidth = Math.floor((availableWidth + overlapWidth) / tabCount);
  const tabWidth = Math.max(38, Math.min(96, fittedWidth));

  tabsEl.style.setProperty('--sheet-tab-width', tabWidth + 'px');
  tabsEl.classList.toggle('is-compact-label', tabWidth < 58);
}

// 화면 가운데에 공용 안내 모달을 표시합니다.
// 등록 완료 알림과 '+ ADD Track' 준비중 안내가 같은 모달을 함께 사용합니다.
function endExpiredSession() {
  currentUserId = null;
  void supabaseClient?.auth.signOut({ scope: 'local' });
}

function showCenteredNotice(titleText, messageText = '', onClose = null) {
  const modal = document.getElementById('service-preparing-modal');
  const title = document.getElementById('service-preparing-title');
  const message = modal?.querySelector('p');
  const requiresLogin = `${titleText}\n${messageText}`.includes('로그인이 필요합니다');

  if (!modal || !title || !message) {
    window.alert(messageText ? `${titleText}\n${messageText}` : titleText);
    if (requiresLogin) endExpiredSession();
    return;
  }

  noticeCloseAction = typeof onClose === 'function'
    ? onClose
    : (requiresLogin ? endExpiredSession : null);
  title.textContent = titleText;
  message.textContent = messageText;
  message.classList.toggle('hidden', !messageText);
  modal.classList.remove('hidden');
}

// + ADD Track을 눌렀을 때 보여주는 안내 문구입니다.
function showAddTrackNotice() {
  showCenteredNotice(
  '서비스 준비 중입니다.',
  '한 탭에 지표가 8개를 넘으면 자동으로 다음 탭이 생성됩니다.'
);
}

// 가운데 공용 안내 모달을 닫습니다.
function closeServicePreparingModal() {
  document.getElementById('service-preparing-modal')?.classList.add('hidden');
  const onClose = noticeCloseAction;
  noticeCloseAction = null;
  onClose?.();
}

// 새 지표 등록이 성공한 뒤 공통으로 처리할 작업입니다.
// 마지막 Track으로 이동 → 화면 갱신 → 입력폼 초기화 → 등록 완료 안내 순서로 실행합니다.
function finishTargetRegistration(currentValueDisplay = '', checkErrorMessage = '') {
  activeTrack = getTrackCount();
  renderTargets();

  document.getElementById('add-form').reset();
  document.getElementById('input-type').value = 'FRED';
  toggleTypeFields();
  toggleTargetValueInput('input-condition', 'input-target-val');

  showCenteredNotice(
    checkErrorMessage ? '등록 되었습니다. 현재값 확인 실패' : '등록 되었습니다.',
    checkErrorMessage || (currentValueDisplay ? `확인된 현재값: ${currentValueDisplay}` : '')
  );
}

// ===== 추적 지표 한 줄 만들기 =====
// 지표 한 개를 화면에 표시할 HTML 문자열로 만듭니다.
// 제목, 현재값, 알림 상태, 상세정보, 수정/삭제 버튼이 모두 여기에서 만들어집니다.
// 수집 결과를 목록에 표시할 상태와 시간으로 변환합니다.
function getCollectionState(item) {
  return item?.last_error
    ? { label: '수집 불가', className: 'text-red-300' }
    : { label: item?.last_value ?? '—', className: 'text-amber-400' };
}

function formatLastCheckedAt(value) {
  if (!value) return '기록 없음';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '기록 없음';
  return date.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
}

async function checkOneTarget(targetId) {
  const button = document.querySelector('[data-check-one="' + targetId + '"]');
  if (button) { button.disabled = true; button.textContent = '확인 요청 중'; }
  try {
    const session = await supabaseClient.auth.getSession();
    const token = session.data.session?.access_token;
    if (!token) throw new Error('로그인이 필요합니다.');
    const response = await fetch(SUPABASE_URL + '/functions/v1/check-one-target', { method: 'POST', headers: { apikey: SUPABASE_KEY, Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: JSON.stringify({ target_id: targetId }) });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || '확인 작업을 시작하지 못했습니다.');
    await new Promise((resolve) => setTimeout(resolve, 3000));
    await fetchTargets();
    const value = result.target?.last_value;
    const valueText = value === null || value === undefined || value === '' ? '' : `현재값: ${value}`;
    showCenteredNotice('현재값 업데이트 완료', valueText);
  } catch (error) {
    const message = error.message || '현재값을 확인하지 못했습니다.';
    if (message.includes('로그인이 필요합니다')) {
      showCenteredNotice(
        '로그인이 필요합니다.',
        '확인을 누르면 로그인 화면으로 돌아갑니다.',
      );
    } else {
      showCenteredNotice('현재값 확인 실패', message);
    }
  } finally {
    if (button) { button.disabled = false; button.textContent = '현재값 확인'; }
  }
}
function renderTargetItem(item, globalIndex, isFirstVisible, isLastVisible) {
  return `
    <div data-target-container="${globalIndex}" class="py-3 border-b border-slate-800/80 first:border-t"
         style="${isFirstVisible ? 'border-top-color: transparent;' : ''}${isLastVisible ? 'border-bottom-color: transparent;' : ''}">
      <div data-target-row class="flex items-center justify-between gap-3 px-2 rounded-lg hover:bg-slate-800/30 transition">
        <div class="flex items-center gap-3 min-w-0 flex-1">
          <i class="touch-drag-handle fa-solid fa-grip-vertical text-slate-600 hover:text-slate-400 px-1" aria-hidden="true"></i>
          <div class="min-w-0 flex-1 py-3">
            <div class="flex items-center gap-2 min-w-0">
              <button type="button" onclick="toggleTargetDetails('${item.id}')" class="min-w-0 flex-1 text-left">
                <span class="flex items-center gap-2">
                  <span class="text-sm font-bold text-white truncate">${escapeHtml(item.title)}</span>
                  <i class="fa-solid ${String(item.id) === expandedTargetId ? 'fa-chevron-up' : 'fa-chevron-down'} text-[10px] text-slate-500"></i>
                </span>
              </button>

            </div>
            <span class="compact-mobile-summary block text-xs text-slate-400 mt-0.5">
              ${item.target_value !== null && item.target_value !== undefined ? `설정: <span class="text-blue-400 font-mono">${item.target_value}</span> | ` : ''}
              현재: <span class="${getCollectionState(item).className} font-mono">${getCollectionState(item).label}</span>
            </span>
            <span class="target-condition-summary block text-xs text-slate-400 mt-0.5 truncate">
              <span class="text-slate-300 font-mono">${getConditionText(item.condition_type)}</span>
              ${item.target_value !== null && item.target_value !== undefined ? ` | 설정: <span class="text-blue-400 font-mono">${item.target_value}</span>` : ''}
              | 현재: <span class="${getCollectionState(item).className} font-mono">${getCollectionState(item).label}</span>
            </span>
          </div>
        </div>
        ${item.url ? `<a href="${escapeHtml(getOriginalUrl(item))}" target="_blank" rel="noopener noreferrer" class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md border border-blue-500/30 bg-blue-500/10 text-blue-300 transition hover:bg-blue-500/20 hover:text-blue-100" title="출처 열기" aria-label="출처 열기"><i class="fa-solid fa-arrow-up-right-from-square text-[8px]"></i></a>` : ''}
        <button type="button" onclick="toggleTargetActive('${item.id}')" class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md transition ${item.is_active !== false ? 'text-amber-400 hover:bg-amber-500/20 hover:text-amber-300' : 'text-slate-600 hover:bg-slate-800 hover:text-slate-400'}" title="${item.is_active !== false ? '알림 끄기' : '알림 켜기'}" aria-label="${item.is_active !== false ? '알림 끄기' : '알림 켜기'}">
          <i class="fa-solid text-[15px] ${item.is_active !== false ? 'fa-bell' : 'fa-bell-slash'}"></i>
        </button>
      </div>
      ${String(item.id) === expandedTargetId ? `
  <div class="mx-2 mb-2 ml-9 rounded-xl border border-slate-700/70 bg-slate-950/60 p-4">
  <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-5 gap-y-3 text-xs">
  ${item.last_error ? `
  <div class="sm:col-span-2 rounded-lg border border-red-400/30 bg-red-950/20 p-3">
    <dt class="text-red-300">수집 오류</dt>
    <dd class="mt-1 break-words leading-relaxed text-red-200">${escapeHtml(item.last_error)}</dd>
    <p class="mt-2 text-xs text-slate-400">다음 정기 수집 때 자동으로 다시 확인합니다.</p>
  </div>
  ` : ''}
  <div class="sm:col-span-2">
  <dt class="text-slate-500">대상 URL</dt>
  <dd class="mt-1 break-all font-mono text-slate-300">${escapeHtml(getOriginalUrl(item) || '—')}</dd>
  </div>
  <div class="sm:col-span-2">
  <dt class="text-slate-500">추출 설정</dt>
  <dd class="mt-1 break-all font-mono text-slate-300">${escapeHtml(item.css_selector || '—')}</dd>
  </div>
  <div>
  <dt class="text-slate-500">알림 조건</dt>
  <dd class="mt-1 text-slate-200">${getConditionText(item.condition_type)}</dd>
  </div>
  <div>
  <dt class="text-slate-500">설정값</dt>
  <dd class="mt-1 font-mono text-blue-400">${item.target_value ?? '—'}</dd>
  </div>
  <div>
  <dt class="text-slate-500">마지막 확인</dt>
  <dd class="mt-1 text-slate-300">${formatLastCheckedAt(item.last_checked_at)}</dd>
  </div>
  </dl>
  <div class="mt-4 flex flex-wrap justify-end gap-2">
  <button type="button" data-check-one="${item.id}" onclick="checkOneTarget(&quot;${item.id}&quot;)" class="mr-auto rounded-md bg-blue-500/10 px-3 py-1.5 text-xs font-semibold text-blue-300 hover:bg-blue-500/20 transition">현재값 확인</button>
  <button onclick="openEditModal('${item.id}')" class="rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-700 transition"><i class="fa-solid fa-pen-to-square mr-1"></i>수정</button>
  <button onclick="handleDeleteTarget('${item.id}')" class="rounded-md bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-500/20 transition"><i class="fa-solid fa-trash-can mr-1"></i>삭제</button>
  </div>
  </div>
  ` : ''}
    </div>
  `;
}

// ===== 현재 Tab의 추적 목록 화면에 표시 =====
// 현재 활성 Track에 속한 최대 8개의 지표를 목록 영역에 표시합니다.
// 먼저 Track 탭을 다시 그리고, 오류/빈 목록/정상 목록 상태를 각각 처리합니다.
function renderTargets() {
  const listEl = document.getElementById('target-list');
  if (!listEl) return;

  normalizeActiveTrack();
  renderTrackTabs();

  if (targetLoadError) {
    listEl.innerHTML = `<p class="text-sm text-amber-300 py-6 text-center"><i class="fa-solid fa-triangle-exclamation mr-2"></i>연결 오류가 발생했습니다.<br><span class="text-xs text-slate-400">로그아웃 후 다시 시도해 주세요.</span></p>`;
    return;
  }

  if (targets.length === 0) {
    listEl.innerHTML = `<p class="text-sm text-slate-500 py-6 text-center"><i class="fa-solid fa-circle-info mr-2"></i>등록된 추적 항목이 없습니다.</p>`;
    return;
  }

  const startIndex = getTrackStartIndex();
  const endIndex = getTrackEndIndex();
  const visibleTargets = targets.slice(startIndex, endIndex);

  listEl.innerHTML = visibleTargets.map((item, localIndex) => {
    const globalIndex = startIndex + localIndex;
    return renderTargetItem(
    item,
    globalIndex,
    localIndex === 0,
    localIndex === visibleTargets.length - 1
  );
}).join('');
}

// 지표 제목을 눌렀을 때 상세정보 영역을 펼치거나 접습니다.
function toggleTargetDetails(id) {
  const targetId = String(id);
  expandedTargetId = expandedTargetId === targetId ? null : targetId;
  renderTargets();
}

// 종 모양 버튼을 눌렀을 때 해당 지표의 알림 활성/비활성 상태를 바꿉니다.
function toggleTargetActive(id) {
  const item = targets.find(target => String(target.id) === String(id));
  if (!item) return;

  pendingToggleId = id;
  const nextIsActive = item.is_active === false;
  const modal = document.getElementById('toggle-alert-modal');
  const message = document.getElementById('toggle-alert-message');

  if (!modal || !message) return;
  message.textContent = nextIsActive
    ? '카카오톡 알림을 켭니다.'
    : '카카오톡 알림을 끕니다.';
  modal.classList.remove('hidden');
}

function closeToggleAlertModal() {
  pendingToggleId = null;
  document.getElementById('toggle-alert-modal')?.classList.add('hidden');
}

async function confirmToggleTarget() {
  if (pendingToggleId === null) return;

  const id = pendingToggleId;
  const index = targets.findIndex(item => String(item.id) === String(id));
  if (index === -1) return closeToggleAlertModal();

  const item = targets[index];
  const nextIsActive = item.is_active === false;

  try {
    if (supabaseClient && !String(id).startsWith('local_')) {
      const userId = await requireCurrentUserId();
      if (!userId) return;
      const { error } = await supabaseClient
        .from('targets')
        .update({ is_active: nextIsActive })
        .eq('id', id)
        .eq('user_id', userId);
      if (error) throw error;
    }

    targets[index] = { ...item, is_active: nextIsActive };
    closeToggleAlertModal();
    renderTargets();
  } catch (err) {
    console.error(err);
    closeToggleAlertModal();
  }
}

// ===== 마우스·터치 공통 드래그 순서 변경 =====
// 마우스는 행 전체에서 시작하고, 터치는 점 6개를 길게 눌렀을 때 시작합니다.
function createPointerDragState() {
  return {
    timer: null,
    pointerId: null,
    pointerType: null,
    sourceIndex: null,
    sourceRow: null,
    sourceContainer: null,
    active: false,
    startX: 0,
    startY: 0,
    offsetX: 0,
    offsetY: 0,
    preview: null,
    scrollFrame: null,
    lastClientX: 0,
    lastClientY: 0
  };
}

function handlePointerDragStart(event) {
  if (event.button !== undefined && event.button !== 0) return;

  const target = event.target instanceof Element ? event.target : null;
  const sourceRow = target?.closest('[data-target-row]');
  const sourceContainer = target?.closest('[data-target-container]');
  if (!sourceRow || !sourceContainer) return;

  const isMouse = event.pointerType === 'mouse';
  if (!isMouse && !target.closest('.touch-drag-handle')) return;

  cancelPointerDrag();

  pointerDragState.pointerId = event.pointerId;
  pointerDragState.pointerType = event.pointerType;
  pointerDragState.sourceIndex = Number(sourceContainer.dataset.targetContainer);
  pointerDragState.sourceRow = sourceRow;
  pointerDragState.sourceContainer = sourceContainer;
  pointerDragState.startX = event.clientX;
  pointerDragState.startY = event.clientY;

  if (!isMouse) {
    pointerDragState.timer = setTimeout(() => {
      beginPointerDrag(pointerDragState.startX, pointerDragState.startY);
    }, 450);
  }
}

function handlePointerDragMove(event) {
  if (event.pointerId !== pointerDragState.pointerId) return;

  if (!pointerDragState.active) {
    if (pointerDragState.pointerType !== 'mouse') return;

    const distance = Math.hypot(
      event.clientX - pointerDragState.startX,
      event.clientY - pointerDragState.startY
    );
    if (distance < 4) return;
    beginPointerDrag(event.clientX, event.clientY);
  }

  event.preventDefault();
  moveDragPreview(event.clientX, event.clientY);
  updatePointerDropTarget(event.clientX, event.clientY);
  updateDownwardAutoScroll(event.clientX, event.clientY);
}

function handlePointerDragEnd(event) {
  if (event.pointerId !== pointerDragState.pointerId) return;

  clearTimeout(pointerDragState.timer);
  if (!pointerDragState.active) {
    cancelPointerDrag();
    return;
  }

  const element = document.elementFromPoint(event.clientX, event.clientY);
  const tab = element?.closest('.sheet-tab');
  const container = element?.closest('[data-target-container]');

  if (
    tab
    && !tab.classList.contains('sheet-tab-add')
    && Number(tab.dataset.track) !== activeTrack
  ) {
    moveDraggedItemToTrack(Number(tab.dataset.track));
  } else if (container || dropIndicatorIndex !== null) {
    moveDraggedItemWithinTrack(
      dropIndicatorIndex ?? Number(container.dataset.targetContainer)
    );
  }

  suppressClickAfterDrag();
  cancelPointerDrag();
}

function handlePointerDragCancel(event) {
  if (event.pointerId !== pointerDragState.pointerId) return;
  cancelPointerDrag();
}

function beginPointerDrag(clientX, clientY) {
  if (pointerDragState.active || !pointerDragState.sourceRow) return;

  const sourceRect = pointerDragState.sourceRow.getBoundingClientRect();
  pointerDragState.active = true;
  draggedItemIndex = pointerDragState.sourceIndex;
  document.body.classList.add('is-pointer-dragging');
  pointerDragState.offsetX = pointerDragState.startX - sourceRect.left;
  pointerDragState.offsetY = pointerDragState.startY - sourceRect.top;

  pointerDragState.sourceContainer.style.setProperty('opacity', '.4', 'important');

  const preview = pointerDragState.sourceRow.cloneNode(true);
  preview.classList.add('drag-preview');
  preview.style.width = sourceRect.width + 'px';
  preview.style.height = sourceRect.height + 'px';
  document.body.appendChild(preview);
  pointerDragState.preview = preview;

  moveDragPreview(clientX, clientY);
  document.querySelector('.sheet-tabs')?.classList.add('is-dragging');
  document.querySelectorAll('.sheet-tab').forEach(tab => {
    const isAllowed = !tab.classList.contains('sheet-tab-add')
      && Number(tab.dataset.track) !== activeTrack;
    tab.classList.toggle('is-drop-allowed', isAllowed);
    tab.classList.toggle('is-drop-forbidden', !isAllowed);
  });
}

function moveDragPreview(clientX, clientY) {
  if (!pointerDragState.preview) return;
  pointerDragState.preview.style.left = (clientX - pointerDragState.offsetX) + 'px';
  pointerDragState.preview.style.top = (clientY - pointerDragState.offsetY) + 'px';
}

// 드래그 중 화면 하단에 가까워질 때 아래 방향으로만 자동 스크롤합니다.
function updateDownwardAutoScroll(clientX, clientY) {
  const threshold = 80;
  pointerDragState.lastClientX = clientX;
  pointerDragState.lastClientY = clientY;

  if (window.innerHeight - clientY >= threshold) {
    stopDownwardAutoScroll();
    return;
  }

  if (pointerDragState.scrollFrame === null) {
    pointerDragState.scrollFrame = requestAnimationFrame(runDownwardAutoScroll);
  }
}

function runDownwardAutoScroll() {
  if (!pointerDragState.active) {
    stopDownwardAutoScroll();
    return;
  }

  const threshold = 80;
  const distanceFromBottom = Math.max(
    0,
    window.innerHeight - pointerDragState.lastClientY
  );

  if (distanceFromBottom >= threshold) {
    stopDownwardAutoScroll();
    return;
  }

  const speed = Math.max(
    2,
    Math.ceil((threshold - distanceFromBottom) / threshold * 14)
  );
  const previousScrollY = window.scrollY;
  window.scrollBy(0, speed);

  updatePointerDropTarget(
    pointerDragState.lastClientX,
    pointerDragState.lastClientY
  );

  if (window.scrollY === previousScrollY) {
    stopDownwardAutoScroll();
    return;
  }

  pointerDragState.scrollFrame = requestAnimationFrame(runDownwardAutoScroll);
}

function stopDownwardAutoScroll() {
  if (pointerDragState.scrollFrame !== null) {
    cancelAnimationFrame(pointerDragState.scrollFrame);
    pointerDragState.scrollFrame = null;
  }
}

function updatePointerDropTarget(clientX, clientY) {
  const element = document.elementFromPoint(clientX, clientY);
  const tab = element?.closest('.sheet-tab');
  const container = element?.closest('[data-target-container]');

  document.querySelectorAll('.sheet-tab.is-drag-over').forEach(item => {
    item.classList.remove('is-drag-over');
  });

  if (tab) {
    clearDropIndicator();

    const isAllowed = !tab.classList.contains('sheet-tab-add')
      && Number(tab.dataset.track) !== activeTrack;
    if (isAllowed) tab.classList.add('is-drag-over');
    return;
  }

  if (!container) {
    clearDropIndicator();
    return;
  }

  const targetIndex = Number(container.dataset.targetContainer);
  if (targetIndex === draggedItemIndex) {
    clearDropIndicator();
    return;
  }

  const row = container.querySelector('[data-target-row]') || container;
  const rect = row.getBoundingClientRect();
  setDropIndicator(clientY < rect.top + rect.height / 2 ? targetIndex : targetIndex + 1);
}

function moveDraggedItemWithinTrack(insertIndex) {
  if (draggedItemIndex === null) return;

  const trackStart = getTrackStartIndex();
  const trackEnd = getTrackEndIndex();
  if (
    draggedItemIndex < trackStart ||
    draggedItemIndex >= trackEnd ||
    insertIndex < trackStart ||
    insertIndex > trackEnd
  ) return;

  const destinationIndex = draggedItemIndex < insertIndex ? insertIndex - 1 : insertIndex;
  if (destinationIndex === draggedItemIndex) return;

  const [movedItem] = targets.splice(draggedItemIndex, 1);
  if (!movedItem) return;
  targets.splice(destinationIndex, 0, movedItem);

  updateDisplayOrder();
  renderTargets();
  saveOrderToDb();
}

function moveDraggedItemToTrack(targetTrack) {
  if (draggedItemIndex === null) return;

  const sourceTrack = Math.floor(draggedItemIndex / ITEMS_PER_TRACK) + 1;
  if (targetTrack === sourceTrack) return;

  const [movedItem] = targets.splice(draggedItemIndex, 1);
  if (!movedItem) return;

  const insertIndex = targetTrack < sourceTrack
    ? targetTrack * ITEMS_PER_TRACK - 1
    : (targetTrack - 1) * ITEMS_PER_TRACK;

  targets.splice(insertIndex, 0, movedItem);
  updateDisplayOrder();
  renderTargets();
  saveOrderToDb();
}

function setDropIndicator(globalInsertIndex) {
  if (dropIndicatorIndex === globalInsertIndex) return;

  clearDropIndicator();
  dropIndicatorIndex = globalInsertIndex;

  const containers = [...document.querySelectorAll('[data-target-container]')];
  const localInsertIndex = globalInsertIndex - getTrackStartIndex();

  if (localInsertIndex >= 0 && localInsertIndex < containers.length) {
    containers[localInsertIndex]?.style.setProperty('box-shadow', 'inset 0 1px 0 white');
  } else if (containers.length > 0) {
    containers[containers.length - 1]?.style.setProperty('box-shadow', 'inset 0 -1px 0 white');
  }
}

function clearDropIndicator() {
  document.querySelectorAll('[data-target-container]').forEach(container => {
    container.style.removeProperty('box-shadow');
  });
  dropIndicatorIndex = null;
}

function cancelPointerDrag() {
  clearTimeout(pointerDragState.timer);
  stopDownwardAutoScroll();
  document.body.classList.remove('is-pointer-dragging');
  pointerDragState.sourceContainer?.style.removeProperty('opacity');
  pointerDragState.preview?.remove();

  document.querySelector('.sheet-tabs')?.classList.remove('is-dragging');
  document.querySelectorAll('.sheet-tab').forEach(tab => {
    tab.classList.remove(
      'is-drag-over',
      'is-drop-allowed',
      'is-drop-forbidden'
    );
  });
  clearDropIndicator();

  draggedItemIndex = null;
  pointerDragState = createPointerDragState();
}

// 현재 targets 배열 순서를 display_order 값에 다시 기록합니다.
function updateDisplayOrder() {
  targets.forEach((item, index) => {
    item.display_order = index;
  });
}

// ===== 변경된 순서를 DB에 저장 =====
// 드래그로 바뀐 전체 순서를 DB의 display_order에 저장합니다.
// 저장 실패 시 서버 목록을 다시 불러와 원래 순서로 복구합니다.
async function saveOrderToDb() {
  if (!supabaseClient) return;
  const userId = await requireCurrentUserId();
  if (!userId) return;

  try {
    const results = await Promise.all(targets.map((target, displayOrder) =>
    supabaseClient
    .from('targets')
    .update({ display_order: displayOrder })
    .eq('id', target.id)
    .eq('user_id', userId)
    ));
    const failed = results.find((result) => result.error);
    if (failed?.error) throw failed.error;
  } catch (error) {
    console.error('Target order save error:', error);
    await fetchTargets();
    window.alert('순서를 저장하지 못해 기존 순서로 되돌렸습니다.');
  }
}

// ===== 새 추적 지표 등록 =====
// '추적 항목 등록하기' 버튼의 핵심 등록 함수입니다.
// 입력값을 읽어 데이터 소스별 설정을 만들고 DB에 새 지표를 저장합니다.
async function handleAddTarget(e) {
  e.preventDefault();

  if (targets.length >= MAX_TARGETS) {
    window.alert(`추적 지표는 최대 ${MAX_TARGETS}개까지 등록할 수 있습니다.`);
    return;
  }

  const title = document.getElementById('input-title').value.trim();
  const type = document.getElementById('input-type').value;
  const conditionType = document.getElementById('input-condition').value;
  const targetValStr = document.getElementById('input-target-val').value.trim();
  const targetVal = conditionType === 'changed' || targetValStr === ''
    ? null
    : parseFloat(targetValStr);

  let url = '';
  let cssSelector = '';
  let sourceType = '';
  let sourceConfig = {};

  if (type === 'FRED') {
    const seriesId = document.getElementById('input-fred-id').value.trim().toUpperCase();
    url = `https://fred.stlouisfed.org/series/${encodeURIComponent(seriesId)}`;
    cssSelector = 'API:observations[0].value';
    sourceType = 'fred';
    sourceConfig = { series_id: seriesId };
  } else if (type === 'BOK') {
    const statCode = document.getElementById('input-bok-code').value.trim().toUpperCase();
    const itemCode = document.getElementById('input-bok-item-code').value.trim();
    const dataCycle = document.getElementById('input-bok-cycle').value;
    url = 'https://ecos.bok.or.kr/';
    cssSelector = 'API:StatisticSearch.row[0].DATA_VALUE';
    sourceType = 'ecos';
    sourceConfig = { stat_code: statCode, item_code: itemCode, data_cycle: dataCycle };
  }

  const userId = await requireCurrentUserId();
  if (!userId) return;

  const newItem = {
    id: 'local_' + Date.now(),
    user_id: userId,
    title,

    source_type: sourceType,
    source_config: sourceConfig,
    condition_type: conditionType,
    target_value: targetVal,
    last_value: null,
    is_active: true,
    display_order: targets.length
  };

  if (supabaseClient) {
    try {
      const { data, error } = await supabaseClient
      .from('targets')
      .insert([{
        user_id: userId,
        title,
        url,
        css_selector: cssSelector,
        source_type: sourceType,
        source_config: sourceConfig,
        condition_type: conditionType,
        target_value: targetVal,
        last_value: null,
        is_active: true,
        display_order: targets.length
      }])
      .select();

      if (!error && data) {
        const insertedTarget = data[0];
        targets.push(insertedTarget);

        try {
          const session = await supabaseClient.auth.getSession();
          const token = session.data.session?.access_token;
          if (!token) throw new Error('로그인이 필요합니다.');

          const response = await fetch(SUPABASE_URL + '/functions/v1/check-one-target', {
            method: 'POST',
            headers: {
              apikey: SUPABASE_KEY,
              Authorization: 'Bearer ' + token,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ target_id: insertedTarget.id }),
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || '현재값을 확인하지 못했습니다.');

          const checkedTarget = result.target || insertedTarget;
          targets[targets.length - 1] = checkedTarget;
          finishTargetRegistration(
            checkedTarget.last_value === null || checkedTarget.last_value === undefined || checkedTarget.last_value === ''
              ? ''
              : String(checkedTarget.last_value)
          );
        } catch (checkError) {
          console.error('New target value check error:', checkError);
          finishTargetRegistration('', checkError?.message || '현재값을 확인하지 못했습니다.');
        }
        return;
      }
      if (error) {
        window.alert('등록 실패: ' + error.message);
        return;
      }
    } catch (error) {
      console.error(error);
      return;
    }
  }

  targets.push(newItem);
  finishTargetRegistration('');
}

// ===== 지표 수정 =====
// 선택한 지표의 현재 값을 수정 모달 입력칸에 채우고 모달을 엽니다.
function openEditModal(id) {
  const item = targets.find(t => String(t.id) === String(id));
  if (!item) return;

  currentEditId = id;
  document.getElementById('edit-title').value = item.title || '';
  document.getElementById('edit-condition').value = item.condition_type || 'changed';
  document.getElementById('edit-target-val').value = item.target_value ?? '';
  toggleTargetValueInput('edit-condition', 'edit-target-val');

  document.getElementById('edit-modal').classList.remove('hidden');
}


// 수정 모달을 닫고 현재 수정 중인 지표 ID를 초기화합니다.
function closeEditModal() {
  document.getElementById('edit-modal').classList.add('hidden');
  currentEditId = null;
}


// 수정 모달에서 변경한 내용을 DB에 저장하고 목록을 갱신합니다.
async function saveEditTarget() {
  if (!currentEditId) return;

  const title = document.getElementById('edit-title').value.trim();
  const conditionType = document.getElementById('edit-condition').value;
  const targetValStr = document.getElementById('edit-target-val').value.trim();
  const targetVal = conditionType === 'changed' || targetValStr === '' ? null : parseFloat(targetValStr);
  const updatedData = {
    title,
    condition_type: conditionType,
    target_value: targetVal
  };

  if (supabaseClient && !currentEditId.toString().startsWith('local_')) {
    try {
      const userId = await requireCurrentUserId();
      if (!userId) return;
      const { error } = await supabaseClient
      .from('targets')
      .update(updatedData)
      .eq('id', currentEditId)
      .eq('user_id', userId);
      if (error) throw error;
    } catch (err) {
      console.error(err);
      return;
    }
  }

  const index = targets.findIndex(t => String(t.id) === String(currentEditId));
  if (index !== -1) {
    targets[index] = { ...targets[index], ...updatedData };
  }

  renderTargets();
  closeEditModal();
}

// ===== 지표 삭제 =====
// 삭제 버튼을 눌렀을 때 바로 삭제하지 않고 확인 모달을 먼저 엽니다.
function handleDeleteTarget(id) {
  currentDeleteId = id;
  document.getElementById('delete-modal').classList.remove('hidden');
}

// 삭제 확인 모달을 닫고 삭제 대상 ID를 초기화합니다.
function closeDeleteModal() {
  document.getElementById('delete-modal').classList.add('hidden');
  currentDeleteId = null;
}

// 삭제 확인 후 실제 DB에서 지표를 삭제하고 화면 목록을 갱신합니다.
async function confirmDeleteTarget() {
  if (!currentDeleteId) return;

  const id = currentDeleteId;
  if (supabaseClient && !id.toString().startsWith('local_')) {
    try {
      const userId = await requireCurrentUserId();
      if (!userId) return;
      const { error } = await supabaseClient
      .from('targets')
      .delete()
      .eq('id', id)
      .eq('user_id', userId);
      if (error) throw error;
    } catch (err) {
      console.error(err);
      return;
    }
  }

  targets = targets.filter(t => String(t.id) !== String(id));
  renderTargets();
  closeDeleteModal();
}

// ===== 화면 표시와 URL 처리에 사용하는 보조 함수 =====
function getConditionText(condition) {
  switch (condition) {
    case 'changed': return '지표값 변동 감지';
    case 'gte': return '설정값 상향 돌파';
    case 'lte': return '설정값 하향 돌파';
    case 'cross': return '설정값 상/하향 돌파';
    default: return condition;
  }
}

// 지표 상세정보의 '원본 URL'을 만듭니다.
// 일반 웹은 저장된 URL을 그대로 사용하고, FRED/ECOS처럼 별도 설정이 있는 데이터는 원본 페이지 주소를 복원합니다.
function getOriginalUrl(item) {
  const url = item?.url || '';
  if (url.includes('api.stlouisfed.org/fred/series/observations')) {
    try {
      const seriesId = new URL(url).searchParams.get('series_id');
      if (seriesId) return `https://fred.stlouisfed.org/series/${encodeURIComponent(seriesId)}`;
    } catch (err) {
      console.error('FRED URL Parse Error:', err);
    }
  }
  if (url.includes('ecos.bok.or.kr/api/StatisticSearch/')) {
    return 'https://ecos.bok.or.kr/';
  }
  return url;
}

// 사용자 입력값을 HTML 안에 안전하게 표시하기 위해 특수문자를 바꿉니다.
// 제목이나 URL에 <, >, &, 따옴표가 들어가도 화면 구조가 깨지지 않게 합니다.
function escapeHtml(str) {
  return String(str || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
