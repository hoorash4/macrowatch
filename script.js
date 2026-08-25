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
const CREDIT_STRESS_HISTORY_MONTHS = 36;
const CREDIT_STRESS_CHART_HEIGHT = 375;
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
    const title = `${item.article_date}: 긍정 ${Math.round(positive)}%, 부정 ${Math.round(negative)}%`;
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

function calculateCorrelation(pairs) {
  if (pairs.length < 2) return null;
  const meanX = pairs.reduce((sum, [x]) => sum + x, 0) / pairs.length;
  const meanY = pairs.reduce((sum, [, y]) => sum + y, 0) / pairs.length;
  const numerator = pairs.reduce((sum, [x, y]) => sum + (x - meanX) * (y - meanY), 0);
  const denominator = Math.sqrt(
    pairs.reduce((sum, [x]) => sum + (x - meanX) ** 2, 0)
    * pairs.reduce((sum, [, y]) => sum + (y - meanY) ** 2, 0),
  );
  return denominator ? numerator / denominator : null;
}

function renderMarketStressDashboard(rows, weeklyRows = []) {
  if (weeklyRows.length) return renderMarketStressAndTensionChart(weeklyRows);
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
  const height = CREDIT_STRESS_CHART_HEIGHT;
  const padding = { top: 20, right: 52, bottom: 32, left: 52 };
  const scores = data.map((row) => Number(row.stress_index));
  const sp500Values = data.map((row) => Number(row.sp500_month_end_close)).filter(Number.isFinite);
  const hasSp500 = sp500Values.length > 1;
  const minimumScore = Math.min(...scores);
  const maximumScore = Math.max(...scores);
  const scoreRange = Math.max(maximumScore - minimumScore, Math.max(maximumScore * 0.1, 1));
  const gridStep = [0.5, 1, 2, 5, 10, 20, 50, 100].find((step) => step >= scoreRange / 4) || 100;
  const axisMinimum = Math.max(0, Math.floor((minimumScore - scoreRange * 0.15) / gridStep) * gridStep);
  const axisMaximum = Math.ceil((maximumScore + scoreRange * 0.15) / gridStep) * gridStep;
  const axisRange = axisMaximum - axisMinimum || gridStep;
  const sp500Minimum = hasSp500 ? Math.min(...sp500Values) : 0;
  const sp500Maximum = hasSp500 ? Math.max(...sp500Values) : 0;
  const sp500Range = Math.max(sp500Maximum - sp500Minimum, Math.max(sp500Maximum * 0.1, 1));
  const sp500Step = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000].find((step) => step >= sp500Range / 4) || 5000;
  const sp500AxisMinimum = hasSp500 ? Math.max(0, Math.floor((sp500Minimum - sp500Range * 0.15) / sp500Step) * sp500Step) : 0;
  const sp500AxisMaximum = hasSp500 ? Math.ceil((sp500Maximum + sp500Range * 0.15) / sp500Step) * sp500Step : 1;
  const sp500AxisRange = sp500AxisMaximum - sp500AxisMinimum || sp500Step;
  const x = (index) => padding.left + ((width - padding.left - padding.right) * index) / Math.max(1, data.length - 1);
  const y = (score) => padding.top + ((height - padding.top - padding.bottom) * (axisMaximum - score)) / axisRange;
  const sp500Y = (value) => padding.top + ((height - padding.top - padding.bottom) * (sp500AxisMaximum - value)) / sp500AxisRange;
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
  const sp500Lines = data.slice(1).map((row, index) => {
    const previous = data[index];
    const previousValue = Number(previous.sp500_month_end_close);
    const currentValue = Number(row.sp500_month_end_close);
    if (!Number.isFinite(previousValue) || !Number.isFinite(currentValue)) return '';
    return `<line x1="${x(index)}" y1="${sp500Y(previousValue)}" x2="${x(index + 1)}" y2="${sp500Y(currentValue)}" stroke="#6b7280" stroke-width="2.25" stroke-linecap="round"/>`;
  }).join('');
  const sp500Dots = data.map((row, index) => {
    const value = Number(row.sp500_month_end_close);
    if (!Number.isFinite(value)) return '';
    return `<circle cx="${x(index)}" cy="${sp500Y(value)}" r="3.25" fill="#6b7280" tabindex="0"><title>${row.month}\nS&P 500 월말 종가: ${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}</title></circle>`;
  }).join('');
  const sp500Axis = hasSp500 ? Array.from(
    { length: Math.round(sp500AxisRange / sp500Step) + 1 },
    (_, index) => sp500AxisMinimum + index * sp500Step,
  ).map((value) => `<text x="${width - padding.right + 9}" y="${sp500Y(value) + 3}" fill="#6b7280" font-size="10">${value.toLocaleString('en-US', { maximumFractionDigits: 0 })}</text>`).join('') : '';
  const correlationPairs = data
    .map((row) => [Number(row.stress_index), Number(row.sp500_month_end_close)])
    .filter(([stress, sp500]) => Number.isFinite(stress) && Number.isFinite(sp500));
  const correlation = calculateCorrelation(correlationPairs);
  const grid = Array.from({ length: Math.round(axisRange / gridStep) + 1 }, (_, index) => axisMinimum + index * gridStep)
    .map((score) => `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(score)}" y2="${y(score)}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text x="${padding.left - 9}" y="${y(score) + 3}" text-anchor="end" fill="#64748b" font-size="10">${Number.isInteger(score) ? score : score.toFixed(1)}</text>`).join('');
  chart.innerHTML = `<div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="미국 시장 스트레스 지수와 S&P 500 월말 종가 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${sp500Axis}${lines}${sp500Lines}${dots}${sp500Dots}${labels}</svg></div><div class="mt-4 flex flex-wrap items-center justify-between gap-x-5 gap-y-2 text-xs text-slate-400"><div class="flex flex-wrap gap-x-5 gap-y-2"><span class="inline-flex items-center gap-2"><i class="h-0.5 w-5 bg-teal-600"></i>US-MSI</span><span class="inline-flex items-center gap-2"><i class="h-0.5 w-5 border-t-2 border-dashed border-amber-600"></i>US-MSI 잠정치</span>${hasSp500 ? '<span class="inline-flex items-center gap-2"><i class="h-0.5 w-5 bg-gray-500"></i>S&P 500 월말 종가</span>' : ''}</div><span>월 단위로 업데이트됩니다.</span></div>${correlation == null ? '' : `<p class="mt-2 text-right text-[11px] text-slate-500">US-MSI·S&P 500 동일 월 상관계수: r = ${correlation.toFixed(2)}</p>`}`;
}

function renderMarketStressAndTensionChart(weeklyRows) {
  const chart = document.getElementById('credit-stress-chart');
  const weekly = [...weeklyRows].filter((row) => Number.isFinite(Number(row.tension_index))).sort((a, b) => String(a.week).localeCompare(String(b.week)));
  if (!chart || !weekly.length) return;
  const width = 920, height = CREDIT_STRESS_CHART_HEIGHT, padding = { top: 20, right: 52, bottom: 32, left: 52 };
  const dates = weekly.map((row) => new Date(row.week).getTime());
  const start = Math.min(...dates), end = Math.max(...dates), x = (value) => padding.left + ((new Date(value).getTime() - start) / Math.max(1, end - start)) * (width - padding.left - padding.right);
  const values = weekly.map((row) => Number(row.tension_index)), minimum = Math.min(...values), maximum = Math.max(...values), range = Math.max(maximum - minimum, 1), lower = minimum - range * .1, upper = maximum + range * .1, y = (value) => padding.top + ((height - padding.top - padding.bottom) * (upper - value)) / (upper - lower);
  const sp500Values = weekly.map((row) => Number(row.sp500_friday_close)).filter(Number.isFinite);
  const hasSp500 = sp500Values.length > 1;
  const sp500Minimum = hasSp500 ? Math.min(...sp500Values) : 0;
  const sp500Maximum = hasSp500 ? Math.max(...sp500Values) : 1;
  const sp500Range = Math.max(sp500Maximum - sp500Minimum, Math.max(sp500Maximum * 0.1, 1));
  const sp500Step = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000].find((step) => step >= sp500Range / 4) || 5000;
  const sp500Lower = hasSp500 ? Math.max(0, Math.floor((sp500Minimum - sp500Range * .1) / sp500Step) * sp500Step) : 0;
  const sp500Upper = hasSp500 ? Math.ceil((sp500Maximum + sp500Range * .1) / sp500Step) * sp500Step : 1;
  const sp500Y = (value) => padding.top + ((height - padding.top - padding.bottom) * (sp500Upper - value)) / Math.max(1, sp500Upper - sp500Lower);
  const yearRows = weekly.filter((row, index) => index === 0 || String(row.week).slice(0, 4) !== String(weekly[index - 1].week).slice(0, 4));
  const yearGuides = yearRows.slice(1).map((row) => `<line x1="${x(row.week)}" x2="${x(row.week)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>`).join('');
  const years = yearRows.slice(1).map((row) => `<text x="${x(row.week)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${String(row.week).slice(0, 4)}</text>`).join('');
  const ticks = Array.from({ length: 5 }, (_, index) => index / 4);
  const grid = ticks.map((ratio) => {
    const value = upper - (upper - lower) * ratio;
    const py = padding.top + (height - padding.top - padding.bottom) * ratio;
    return `<line x1="${padding.left}" x2="${width - padding.right}" y1="${py}" y2="${py}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text x="${padding.left - 9}" y="${py + 3}" text-anchor="end" fill="#64748b" font-size="10">${value.toFixed(1)}</text>`;
  }).join('');
  const sp500Axis = hasSp500 ? Array.from(
    { length: Math.round((sp500Upper - sp500Lower) / sp500Step) + 1 },
    (_, index) => sp500Lower + index * sp500Step,
  ).map((value) => `<text x="${width - padding.right + 9}" y="${sp500Y(value) + 3}" fill="#6b7280" font-size="10">${value.toLocaleString('en-US')}</text>`).join('') : '';
  const sp500Lines = weekly.slice(1).map((row, index) => {
    const previous = weekly[index];
    const previousValue = Number(previous.sp500_friday_close);
    const currentValue = Number(row.sp500_friday_close);
    if (!Number.isFinite(previousValue) || !Number.isFinite(currentValue)) return '';
    return `<line x1="${x(previous.week)}" y1="${sp500Y(previousValue)}" x2="${x(row.week)}" y2="${sp500Y(currentValue)}" stroke="#6b7280" stroke-width="2" stroke-linecap="round"/>`;
  }).join('');
  const weeklyPaths = [];
  let weeklyPath = '';
  let weeklyPathIsProvisional = null;
  const finishWeeklyPath = () => {
    if (!weeklyPath) return;
    weeklyPaths.push(`<path d="${weeklyPath}" fill="none" stroke="${weeklyPathIsProvisional ? '#d97706' : '#00838c'}" stroke-width="${weeklyPathIsProvisional ? '3.25' : '3.25'}" stroke-linecap="round"${weeklyPathIsProvisional ? ' stroke-dasharray="4 3"' : ''}/>`);
    weeklyPath = '';
  };
  weekly.slice(1).forEach((row, index) => {
    const previous = weekly[index];
    const provisional = Boolean(previous.is_provisional || row.is_provisional);
    const endPoint = `${x(row.week)} ${y(Number(row.tension_index))}`;
    if (weeklyPathIsProvisional !== provisional) {
      finishWeeklyPath();
      weeklyPathIsProvisional = provisional;
      weeklyPath = `M ${x(previous.week)} ${y(Number(previous.tension_index))} L ${endPoint}`;
      return;
    }
    weeklyPath += ` L ${endPoint}`;
  });
  finishWeeklyPath();
  chart.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="미국 주간 시장 스트레스 지수와 S&P 500 주간 종가 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${yearGuides}${sp500Axis}${sp500Lines}${weeklyPaths.join('')}${years}</svg>`;
  const svg = chart.querySelector('svg');
  if (!svg) return;
  const createSvgElement = (name, attributes) => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  };
  const hoverGuide = createSvgElement('line', {
    y1: padding.top,
    y2: height - padding.bottom,
    stroke: '#94a3b8',
    'stroke-width': 0.75,
    'stroke-dasharray': '3 4',
    'pointer-events': 'none',
    visibility: 'hidden',
  });
  const hoverValue = createSvgElement('text', {
    'text-anchor': 'middle',
    fill: '#334155',
    'font-size': 11,
    'font-weight': 700,
    stroke: '#f8fafc',
    'stroke-width': 4,
    'paint-order': 'stroke',
    'pointer-events': 'none',
    visibility: 'hidden',
  });
  const hoverPeriod = createSvgElement('text', {
    'text-anchor': 'middle',
    fill: '#64748b',
    'font-size': 10,
    'pointer-events': 'none',
    visibility: 'hidden',
  });
  svg.append(hoverGuide, hoverValue, hoverPeriod);
  const showHover = (week) => {
    const nearest = weekly.reduce((closest, row) => (
      Math.abs(x(row.week) - x(week)) < Math.abs(x(closest.week) - x(week)) ? row : closest
    ));
    const pointX = x(nearest.week);
    const [, month, day] = String(nearest.week).split('-').map(Number);
    hoverGuide.setAttribute('x1', pointX);
    hoverGuide.setAttribute('x2', pointX);
    hoverGuide.setAttribute('visibility', 'visible');
    hoverValue.setAttribute('x', pointX);
    hoverValue.setAttribute('y', padding.top + 11);
    hoverValue.setAttribute('visibility', 'visible');
    hoverValue.textContent = Number(nearest.tension_index).toFixed(2);
    hoverPeriod.setAttribute('x', pointX);
    hoverPeriod.setAttribute('y', height - padding.bottom + 12);
    hoverPeriod.setAttribute('visibility', 'visible');
    hoverPeriod.textContent = `${month}월 ${Math.ceil(day / 7)}주`;
  };
  const setHover = (event) => {
    const bounds = svg.getBoundingClientRect();
    const pointerX = ((event.clientX - bounds.left) / bounds.width) * width;
    const nearest = weekly.reduce((closest, row) => (
      Math.abs(x(row.week) - pointerX) < Math.abs(x(closest.week) - pointerX) ? row : closest
    ));
    showHover(nearest.week);
    window.dispatchEvent(new CustomEvent('macrowatch:market-stress-hover', {
      detail: { active: true, source: 'stress', week: nearest.week },
    }));
  };
  const clearHover = () => {
    hoverGuide.setAttribute('visibility', 'hidden');
    hoverValue.setAttribute('visibility', 'hidden');
    hoverPeriod.setAttribute('visibility', 'hidden');
  };
  const handleSharedHover = ({ detail }) => {
    if (detail.source === 'stress') return;
    if (detail.active) showHover(detail.week);
    else clearHover();
  };
  if (chart._marketStressHoverListener) {
    window.removeEventListener('macrowatch:market-stress-hover', chart._marketStressHoverListener);
  }
  chart._marketStressHoverListener = handleSharedHover;
  window.addEventListener('macrowatch:market-stress-hover', handleSharedHover);
  svg.addEventListener('pointermove', setHover);
  svg.addEventListener('pointerleave', () => {
    clearHover();
    window.dispatchEvent(new CustomEvent('macrowatch:market-stress-hover', {
      detail: { active: false, source: 'stress' },
    }));
  });
}

function renderWeeklyMomentumChart({ chartId, rows, valueKey, source, emptyMessage, ariaLabel, lineColor, averageColor, secondaryValueKey = null, secondaryAverageColor = null, showChanges = true, invertVertical = false, domainStart = null, domainEnd = null }) {
  const chart = document.getElementById(chartId);
  if (!chart) return;
  const levels = [...rows]
    .map((row) => ({ ...row, value: toCreditStressNumber(row[valueKey]) }))
    .filter((row) => Number.isFinite(row.value))
    .sort((a, b) => String(a.month).localeCompare(String(b.month)));
  const changes = levels.slice(1).map((row, index) => ({
    ...row,
    value: row.value - levels[index].value,
  }));
  const secondaryLevels = secondaryValueKey ? [...rows]
    .map((row) => ({ ...row, value: toCreditStressNumber(row[secondaryValueKey]) }))
    .filter((row) => Number.isFinite(row.value))
    .sort((a, b) => String(a.month).localeCompare(String(b.month))) : [];
  const secondaryChanges = secondaryLevels.slice(1).map((row, index) => ({
    ...row,
    value: row.value - secondaryLevels[index].value,
  }));
  const secondaryAverages = new Map(secondaryChanges.map((row, index) => {
    const window = secondaryChanges.slice(Math.max(0, index - 3), index + 1);
    return [row.month, window.length === 4 ? window.reduce((sum, item) => sum + item.value, 0) / 4 : null];
  }));
  const data = changes.map((row, index) => {
    const window = changes.slice(Math.max(0, index - 3), index + 1);
    return {
      ...row,
      average: window.length === 4
        ? window.reduce((sum, item) => sum + item.value, 0) / 4
        : null,
      secondaryAverage: secondaryAverages.get(row.month) ?? null,
    };
  });
  if (!data.length) {
    chart.innerHTML = `<div class="flex min-h-40 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">${emptyMessage}</div>`;
    return;
  }
  const width = 920;
  const height = 190;
  const padding = { top: 18, right: 52, bottom: 32, left: 52 };
  const extent = Math.max(...data.flatMap((row) => [Math.abs(row.value), Math.abs(row.average || 0), Math.abs(row.secondaryAverage || 0)]), 0.005);
  const step = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10].find((value) => value >= extent / 2) || 20;
  const axisMaximum = Math.ceil((extent * 1.15) / step) * step;
  const formatAxisValue = (value) => {
    const digits = Math.abs(value) < 0.1 ? 3 : Math.abs(value) < 1 ? 2 : 1;
    return `${value > 0 ? '+' : ''}${value.toFixed(digits)}`;
  };
  const dates = levels.map((row) => new Date(row.month).getTime());
  const start = domainStart ? new Date(domainStart).getTime() : Math.min(...dates);
  const end = domainEnd ? new Date(domainEnd).getTime() : Math.max(...dates);
  const x = (value) => padding.left + ((new Date(value).getTime() - start) / Math.max(1, end - start)) * (width - padding.left - padding.right);
  const y = (value) => padding.top + ((height - padding.top - padding.bottom) * (invertVertical ? value + axisMaximum : axisMaximum - value)) / (axisMaximum * 2);
  const grid = [-axisMaximum, 0, axisMaximum].map((value) => `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(value)}" y2="${y(value)}" stroke="${value === 0 ? '#536579' : '#dbe3ed'}"${value === 0 ? '' : ' stroke-dasharray="3 4"'}/><text x="${padding.left - 8}" y="${y(value) + 3}" text-anchor="end" fill="#64748b" font-size="10">${formatAxisValue(value)}</text>`).join('');
  const lines = showChanges ? data.slice(1).map((row, index) => `<line x1="${x(data[index].month)}" y1="${y(data[index].value)}" x2="${x(row.month)}" y2="${y(row.value)}" stroke="${lineColor}" stroke-width="1.75" stroke-linecap="round"/>`).join('') : '';
  const averageLines = data.slice(1).map((row, index) => {
    const previous = data[index];
    if (!Number.isFinite(previous.average) || !Number.isFinite(row.average)) return '';
    return `<line x1="${x(previous.month)}" y1="${y(previous.average)}" x2="${x(row.month)}" y2="${y(row.average)}" stroke="${averageColor}" stroke-width="3" stroke-linecap="round"/>`;
  }).join('');
  const secondaryAverageLines = secondaryAverageColor ? data.slice(1).map((row, index) => {
    const previous = data[index];
    if (!Number.isFinite(previous.secondaryAverage) || !Number.isFinite(row.secondaryAverage)) return '';
    return `<line x1="${x(previous.month)}" y1="${y(previous.secondaryAverage)}" x2="${x(row.month)}" y2="${y(row.secondaryAverage)}" stroke="${secondaryAverageColor}" stroke-width="2.5" stroke-opacity="0.48" stroke-linecap="round"/>`;
  }).join('') : '';
  const yearGuides = data.filter((row, index) => index > 0 && String(row.month).slice(0, 4) !== String(data[index - 1].month).slice(0, 4)).map((row) => `<line x1="${x(row.month)}" x2="${x(row.month)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>`).join('');
  chart.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="${ariaLabel}"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${yearGuides}${lines}${secondaryAverageLines}${averageLines}</svg>`;
  const svg = chart.querySelector('svg');
  if (!svg) return;
  const hoverGuide = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  hoverGuide.setAttribute('y1', String(padding.top));
  hoverGuide.setAttribute('y2', String(height - padding.bottom));
  hoverGuide.setAttribute('stroke', '#94a3b8');
  hoverGuide.setAttribute('stroke-width', '0.75');
  hoverGuide.setAttribute('stroke-dasharray', '3 4');
  hoverGuide.setAttribute('pointer-events', 'none');
  hoverGuide.setAttribute('visibility', 'hidden');
  svg.append(hoverGuide);
  const showGuide = (week) => {
    const pointX = x(week);
    hoverGuide.setAttribute('x1', String(pointX));
    hoverGuide.setAttribute('x2', String(pointX));
    hoverGuide.setAttribute('visibility', 'visible');
  };
  const clearGuide = () => hoverGuide.setAttribute('visibility', 'hidden');
  const handleSharedHover = ({ detail }) => {
    if (detail.source === source) return;
    if (detail.active) showGuide(detail.week);
    else clearGuide();
  };
  if (chart._marketStressHoverListener) {
    window.removeEventListener('macrowatch:market-stress-hover', chart._marketStressHoverListener);
  }
  chart._marketStressHoverListener = handleSharedHover;
  window.addEventListener('macrowatch:market-stress-hover', handleSharedHover);
  svg.addEventListener('pointermove', (event) => {
    const bounds = svg.getBoundingClientRect();
    const pointerX = ((event.clientX - bounds.left) / bounds.width) * width;
    const nearest = data.reduce((closest, row) => (
      Math.abs(x(row.month) - pointerX) < Math.abs(x(closest.month) - pointerX) ? row : closest
    ));
    showGuide(nearest.month);
    window.dispatchEvent(new CustomEvent('macrowatch:market-stress-hover', {
      detail: { active: true, source, week: nearest.month },
    }));
  });
  svg.addEventListener('pointerleave', () => {
    clearGuide();
    window.dispatchEvent(new CustomEvent('macrowatch:market-stress-hover', {
      detail: { active: false, source },
    }));
  });
}

function renderCreditConditionsMomentum(rows) {
  const compositeRows = rows
    .map((row) => {
      const credit = toCreditStressNumber(row.financial_conditions_credit_index);
      const risk = toCreditStressNumber(row.financial_conditions_risk_index);
      if (!Number.isFinite(credit) || !Number.isFinite(risk)) return null;
      return { ...row, credit_risk_composite: credit * 0.6 + risk * 0.4 };
    })
    .filter(Boolean);
  renderWeeklyMomentumChart({
    chartId: 'credit-stress-momentum-chart',
    rows: compositeRows,
    valueKey: 'financial_conditions_credit_index',
    source: 'credit',
    emptyMessage: '첫 산출 후 선행 긴장 시그널이 표시됩니다.',
    ariaLabel: '미국 주간 선행 긴장 시그널 추이',
    lineColor: '#c4b5d5',
    averageColor: '#6d4b91',
    secondaryValueKey: 'credit_risk_composite',
    secondaryAverageColor: '#8b6aa9',
    showChanges: false,
    domainStart: rows.at(-1)?.week,
    domainEnd: rows[0]?.week,
  });
}

async function loadMarketTension(monthlyRows = []) {
  if (!supabaseClient) return;
  const weeklyResponse = await supabaseClient
    .from('us_market_tension_weekly')
    .select('week,tension_index,financial_conditions_credit_index,financial_conditions_risk_index,sp500_friday_close,is_provisional')
    .order('week', { ascending: false })
    .limit(160);
  const monthlyResponse = await supabaseClient
    .from('us_market_stress_index_monthly')
    .select('month,stress_index')
    .order('month', { ascending: false })
    .limit(CREDIT_STRESS_HISTORY_MONTHS);
  if (weeklyResponse.error || monthlyResponse.error) return;
  renderMarketStressDashboard(monthlyRows.length ? monthlyRows : monthlyResponse.data || [], weeklyResponse.data || []);
  const weeklyRows = (weeklyResponse.data || []).map((row) => ({ ...row, month: row.week }));
  renderCreditConditionsMomentum(weeklyRows);
}

async function loadMarketStressDashboard() {
  const chart = document.getElementById('credit-stress-chart');
  if (!chart || !supabaseClient) return;
  try {
    const { data, error } = await supabaseClient.from('us_market_stress_index_monthly')
      .select('month,stress_index,is_provisional')
      .order('month', { ascending: false })
      .limit(CREDIT_STRESS_HISTORY_MONTHS);
    if (error) throw error;
    renderMarketStressDashboard(data || []);
    loadMarketTension(data || []);
  } catch (error) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">시장 스트레스 지수를 불러오지 못했습니다.</div>';
  }
}

window.loadMarketStressDashboard = loadMarketStressDashboard;

function renderKoreaStressChart(rows, weeklyKospiRows = []) {
  const chart = document.getElementById('korea-stress-chart');
  const fsiChart = document.getElementById('korea-fsi-chart');
  const data = [...rows]
    .filter((row) => Number.isFinite(Number(row.stress_index)))
    .sort((a, b) => String(a.month).localeCompare(String(b.month)));
  const weeklyKospiSource = [...weeklyKospiRows]
    .filter((row) => Number.isFinite(Number(row.kospi_close)))
    .sort((a, b) => String(a.week).localeCompare(String(b.week)));
  if (!chart) return;
  if (!data.length) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">첫 산출 후 한국 시장 스트레스 지수가 표시됩니다.</div>';
    if (fsiChart) fsiChart.innerHTML = '';
    return;
  }
  const weeklyKospi = weeklyKospiSource;
  const width = 920, height = CREDIT_STRESS_CHART_HEIGHT, padding = { top: 20, right: 58, bottom: 32, left: 52 };
  const dates = [...data.map((row) => new Date(row.month).getTime()), ...weeklyKospi.map((row) => new Date(row.week).getTime())];
  const start = Math.min(...dates), end = Math.max(...dates);
  const x = (month) => padding.left + ((new Date(month).getTime() - start) / Math.max(1, end - start)) * (width - padding.left - padding.right);
  const leftValues = data.map((row) => Number(row.stress_index)).filter(Number.isFinite);
  const min = Math.min(...leftValues), max = Math.max(...leftValues), range = Math.max(max - min, 1);
  const lower = Math.max(0, min - range * 0.12), upper = max + range * 0.12;
  const y = (value) => padding.top + (height - padding.top - padding.bottom) * (upper - value) / Math.max(1, upper - lower);
  const kospiValues = weeklyKospi.map((row) => Number(row.kospi_close)).filter(Number.isFinite);
  const hasKospi = kospiValues.length > 1;
  const kospiMin = hasKospi ? Math.min(...kospiValues) : 0, kospiMax = hasKospi ? Math.max(...kospiValues) : 1;
  const kospiRange = Math.max(kospiMax - kospiMin, 1), kospiLower = Math.max(0, kospiMin - kospiRange * .12), kospiUpper = kospiMax + kospiRange * .12;
  const kospiY = (value) => padding.top + (height - padding.top - padding.bottom) * (kospiUpper - value) / Math.max(1, kospiUpper - kospiLower);
  const grid = Array.from({ length: 5 }, (_, index) => {
    const value = upper - (upper - lower) * index / 4, py = y(value);
    return `<line x1="${padding.left}" x2="${width - padding.right}" y1="${py}" y2="${py}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text x="${padding.left - 9}" y="${py + 3}" text-anchor="end" fill="#64748b" font-size="10">${value.toFixed(1)}</text>`;
  }).join('');
  const years = data.filter((row, index) => index > 0 && String(row.month).slice(0, 4) !== String(data[index - 1].month).slice(0, 4));
  const yearGuides = years.map((row) => `<line x1="${x(row.month)}" x2="${x(row.month)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/><text x="${x(row.month)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${String(row.month).slice(0, 4)}</text>`).join('');
  const draw = (key, mapY, color, dash = '') => data.slice(1).map((row, index) => {
    const before = Number(data[index][key]), current = Number(row[key]);
    return Number.isFinite(before) && Number.isFinite(current) ? `<line x1="${x(data[index].month)}" y1="${mapY(before)}" x2="${x(row.month)}" y2="${mapY(current)}" stroke="${color}" stroke-width="${key === 'stress_index' ? '3.25' : '2'}" stroke-linecap="round"${dash ? ` stroke-dasharray="${dash}"` : ''}/>` : '';
  }).join('');
  const stress = data.slice(1).map((row, index) => {
    const before = data[index], provisional = Boolean(before.is_provisional || row.is_provisional);
    return `<line x1="${x(before.month)}" y1="${y(Number(before.stress_index))}" x2="${x(row.month)}" y2="${y(Number(row.stress_index))}" stroke="${provisional ? '#d97706' : '#00838c'}" stroke-width="3.25" stroke-linecap="round"${provisional ? ' stroke-dasharray="4 3"' : ''}/>`;
  }).join('');
  const kospi = hasKospi ? weeklyKospi.slice(1).map((row, index) => {
    const before = weeklyKospi[index];
    return `<line x1="${x(before.week)}" y1="${kospiY(Number(before.kospi_close))}" x2="${x(row.week)}" y2="${kospiY(Number(row.kospi_close))}" stroke="#6b7280" stroke-width="2" stroke-linecap="round"/>`;
  }).join('') : '';
  const kospiLabels = hasKospi ? [kospiLower, (kospiLower + kospiUpper) / 2, kospiUpper].map((value) => `<text x="${width - padding.right + 8}" y="${kospiY(value) + 3}" fill="#6b7280" font-size="10">${Math.round(value).toLocaleString('en-US')}</text>`).join('') : '';
  chart.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="한국 시장 스트레스 지수와 코스피 주간 종가 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${yearGuides}${kospi}${stress}${kospiLabels}</svg>`;

  const createSvgElement = (name, attributes) => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  };
  const attachHover = ({ host, hoverRows, valueKey, mapY, chartHeight, chartPadding, source, label, showLabels = true }) => {
    const svg = host.querySelector('svg');
    if (!svg || !hoverRows.length) return;
    const guide = createSvgElement('line', { y1: chartPadding.top, y2: chartHeight - chartPadding.bottom, stroke: '#94a3b8', 'stroke-width': .75, 'stroke-dasharray': '3 4', 'pointer-events': 'none', visibility: 'hidden' });
    const value = showLabels ? createSvgElement('text', { 'text-anchor': 'middle', fill: '#334155', 'font-size': 11, 'font-weight': 700, stroke: '#f8fafc', 'stroke-width': 4, 'paint-order': 'stroke', 'pointer-events': 'none', visibility: 'hidden' }) : null;
    const period = showLabels ? createSvgElement('text', { 'text-anchor': 'middle', fill: '#64748b', 'font-size': 10, 'pointer-events': 'none', visibility: 'hidden' }) : null;
    svg.append(guide);
    if (value && period) svg.append(value, period);
    const show = (month) => {
      const nearest = hoverRows.reduce((closest, row) => Math.abs(x(row.month) - x(month)) < Math.abs(x(closest.month) - x(month)) ? row : closest);
      const pointX = x(nearest.month);
      guide.setAttribute('x1', pointX); guide.setAttribute('x2', pointX); guide.setAttribute('visibility', 'visible');
      if (value && period) {
        value.setAttribute('x', pointX); value.setAttribute('y', chartPadding.top + 12); value.setAttribute('visibility', 'visible');
        value.textContent = `${label ? `${label} ` : ''}${Number(nearest[valueKey]).toFixed(2)}`;
        period.setAttribute('x', pointX); period.setAttribute('y', chartHeight - chartPadding.bottom + 12); period.setAttribute('visibility', 'visible');
        period.textContent = String(nearest.month).slice(0, 7);
      }
    };
    const clear = () => [guide, value, period].filter(Boolean).forEach((element) => element.setAttribute('visibility', 'hidden'));
    const onMove = (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = ((event.clientX - bounds.left) / bounds.width) * width;
      const nearest = hoverRows.reduce((closest, row) => Math.abs(x(row.month) - pointerX) < Math.abs(x(closest.month) - pointerX) ? row : closest);
      show(nearest.month);
      window.dispatchEvent(new CustomEvent('macrowatch:korea-stress-hover', { detail: { active: true, source, month: nearest.month } }));
    };
    const onSharedHover = ({ detail }) => {
      if (detail.source === source) return;
      if (detail.active) show(detail.month); else clear();
    };
    if (host._koreaStressHoverListener) window.removeEventListener('macrowatch:korea-stress-hover', host._koreaStressHoverListener);
    host._koreaStressHoverListener = onSharedHover;
    window.addEventListener('macrowatch:korea-stress-hover', onSharedHover);
    svg.addEventListener('pointermove', onMove);
    svg.addEventListener('pointerleave', () => {
      clear();
      window.dispatchEvent(new CustomEvent('macrowatch:korea-stress-hover', { detail: { active: false, source } }));
    });
  };
  attachHover({ host: chart, hoverRows: data, valueKey: 'stress_index', mapY: y, chartHeight: height, chartPadding: padding, source: 'korea-main', label: '' });
  if (!fsiChart) return;
  const fsiRows = data.filter((row) => Number.isFinite(Number(row.bok_fsi)) && Number(row.bok_fsi) !== 0);
  if (!fsiRows.length) {
    fsiChart.innerHTML = '<div class="flex min-h-32 items-center justify-center text-xs text-slate-400">한국은행 FSI 비교 자료가 연결되면 보조지표로 표시됩니다.</div>';
    return;
  }
  const fsiHeight = 148, fsiPadding = { top: 18, right: 58, bottom: 28, left: 52 };
  const fsiValues = fsiRows.map((row) => Number(row.bok_fsi));
  const fsiMin = Math.min(...fsiValues), fsiMax = Math.max(...fsiValues), fsiRange = Math.max(fsiMax - fsiMin, 1);
  const fsiLower = Math.max(0, fsiMin - fsiRange * .12), fsiUpper = fsiMax + fsiRange * .12;
  const fsiY = (value) => fsiPadding.top + (fsiHeight - fsiPadding.top - fsiPadding.bottom) * (fsiUpper - value) / Math.max(1, fsiUpper - fsiLower);
  const fsiGrid = Array.from({ length: 3 }, (_, index) => {
    const chartValue = fsiUpper - (fsiUpper - fsiLower) * index / 2;
    return `<line x1="${fsiPadding.left}" x2="${width - fsiPadding.right}" y1="${fsiY(chartValue)}" y2="${fsiY(chartValue)}" stroke="#e6e1f2" stroke-dasharray="3 4"/><text x="${fsiPadding.left - 9}" y="${fsiY(chartValue) + 3}" text-anchor="end" fill="#7c6b9d" font-size="10">${chartValue.toFixed(1)}</text>`;
  }).join('');
  const fsiLine = fsiRows.slice(1).map((row, index) => `<line x1="${x(fsiRows[index].month)}" y1="${fsiY(Number(fsiRows[index].bok_fsi))}" x2="${x(row.month)}" y2="${fsiY(Number(row.bok_fsi))}" stroke="#6d28d9" stroke-width="2.25" stroke-linecap="round"/>`).join('');
  fsiChart.innerHTML = `<div class="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-600"><i class="h-0.5 w-5 bg-violet-700"></i>한국은행 FSI <span class="font-normal text-slate-400">(보조지표)</span></div><svg class="w-full" style="height:${fsiHeight}px" viewBox="0 0 ${width} ${fsiHeight}" role="img" aria-label="한국은행 금융불안지수 보조지표"><line x1="${fsiPadding.left}" x2="${fsiPadding.left}" y1="${fsiPadding.top}" y2="${fsiHeight - fsiPadding.bottom}" stroke="#b6a8d0"/>${fsiGrid}${fsiLine}</svg>`;
  attachHover({ host: fsiChart, hoverRows: fsiRows, valueKey: 'bok_fsi', mapY: fsiY, chartHeight: fsiHeight, chartPadding: fsiPadding, source: 'korea-fsi', label: 'FSI', showLabels: false });
}

async function fetchBokFsiForDisplay() {
  const response = await fetch('https://snapshot.bok.or.kr/api/chart/getChart?id=1583');
  if (!response.ok) throw new Error('BOK FSI request failed');
  const payload = await response.json();
  const csv = payload?.data?.chart_opt?.data?.csv;
  if (typeof csv !== 'string') throw new Error('BOK FSI response is invalid');
  return csv.trim().split(/\r?\n/).slice(1).reduce((values, line) => {
    const [period, rawValue] = line.split(',');
    const value = Number(rawValue);
    if (!Number.isFinite(Number(period)) || !Number.isFinite(value)) return values;
    const month = new Date(Number(period)).toISOString().slice(0, 7) + '-01';
    values[month] = value;
    return values;
  }, {});
}

async function loadKoreaStressDashboard() {
  const chart = document.getElementById('korea-stress-chart');
  if (!chart || !supabaseClient) return;
  try {
    const [monthlyResponse, weeklyResponse] = await Promise.all([
      supabaseClient.from('korea_market_stress_monthly')
        .select('month,stress_index,bok_fsi,kospi_close,is_provisional')
        .order('month', { ascending: false }).limit(CREDIT_STRESS_HISTORY_MONTHS),
      supabaseClient.from('korea_market_stress_weekly')
        .select('week,kospi_close,corporate_credit_spread,short_term_funding_spread')
        .order('week', { ascending: false }).limit(CREDIT_STRESS_HISTORY_MONTHS * 6),
    ]);
    if (monthlyResponse.error) throw monthlyResponse.error;
    if (weeklyResponse.error) throw weeklyResponse.error;
    let displayRows = monthlyResponse.data || [];
    if (!displayRows.some((row) => Number.isFinite(Number(row.bok_fsi)))) {
      try {
        const officialFsi = await fetchBokFsiForDisplay();
        displayRows = displayRows.map((row) => ({ ...row, bok_fsi: officialFsi[String(row.month).slice(0, 10)] ?? null }));
      } catch (_) {
        // K-MSI 자체는 DB 자료만으로 계속 표시한다.
      }
    }
    renderKoreaStressChart(displayRows, weeklyResponse.data || []);
  } catch (error) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">한국 시장 스트레스 데이터를 불러오지 못했습니다.</div>';
  }
}

window.loadKoreaStressDashboard = loadKoreaStressDashboard;

function toCreditStressNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function addBankruptcyTrailingAverage(rows) {
  const filings = [];
  return rows.map((row) => {
    const value = toCreditStressNumber(row.business_bankruptcy_filings);
    if (Number.isFinite(value) && value > 0) filings.push(value);
    const average = filings.length
      ? filings.slice(-3).reduce((sum, filing) => sum + filing, 0) / Math.min(3, filings.length)
      : null;
    return { ...row, business_bankruptcy_filings_3m_average: row.is_latest ? null : average };
  });
}

function renderCreditStressComponents(rows) {
  const chart = document.getElementById('credit-stress-components-chart');
  if (!chart) return;
  if (!rows.length) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">첫 수집 후 장기 신용위험 추이가 표시됩니다.</div>';
    return;
  }
  const data = addBankruptcyTrailingAverage(
    [...rows].sort((a, b) => String(a.month).localeCompare(String(b.month))),
  ).slice(-CREDIT_STRESS_HISTORY_MONTHS);
  const series = [
    { key: 'high_yield_oas_pct', label: '하이일드 스프레드', color: '#285e8e', digits: 2, suffix: '%p' },
    { key: 'financial_conditions_credit_index', label: '금융 신용여건', color: '#b91c1c', digits: 3, suffix: '' },
    { key: 'business_bankruptcy_filings_3m_average', label: '기업 파산보호 신청(3개월 평균)', color: '#b7791f', digits: 0, suffix: '건' },
  ];
  const width = 920;
  const height = CREDIT_STRESS_CHART_HEIGHT;
  const padding = { top: 20, right: 52, bottom: 32, left: 52 };
  const dates = data.map((row) => new Date(row.month).getTime());
  const firstDate = Math.min(...dates);
  const lastDate = Math.max(...dates);
  const x = (index) => padding.left + ((dates[index] - firstDate) / Math.max(1, lastDate - firstDate)) * (width - padding.left - padding.right);
  const scaleFor = (item, clampAtZero = false) => {
    const values = data.map((row) => toCreditStressNumber(row[item.key])).filter(Number.isFinite);
    const minimum = Math.min(...values), maximum = Math.max(...values), range = Math.max(maximum - minimum, 0.01);
    const lower = clampAtZero ? Math.max(0, minimum - range * .1) : minimum - range * .1;
    const upper = maximum + range * .1;
    return { lower, upper, y: (value) => padding.top + ((height - padding.top - padding.bottom) * (upper - value)) / (upper - lower) };
  };
  const [highYield, conditions, bankruptcy] = series;
  const highYieldScale = scaleFor(highYield);
  const conditionsScale = scaleFor(conditions);
  const bankruptcyScale = scaleFor(bankruptcy, true);
  const pathFor = (item, scale, includeLatest = true) => {
    let path = '';
    let connected = false;
    data.forEach((row, index) => {
      if (row.is_latest && !includeLatest) return;
      const value = toCreditStressNumber(row[item.key]);
      if (!Number.isFinite(value)) { connected = false; return; }
      path += `${connected ? 'L' : 'M'}${x(index).toFixed(1)},${scale.y(value).toFixed(1)}`;
      connected = true;
    });
    return path;
  };
  const latestSegmentFor = (item, scale) => {
    const index = data.findIndex((row) => row.is_latest);
    if (index < 1) return '';
    const previous = toCreditStressNumber(data[index - 1][item.key]);
    const current = toCreditStressNumber(data[index][item.key]);
    if (!Number.isFinite(previous) || !Number.isFinite(current)) return '';
    return `<line x1="${x(index - 1).toFixed(1)}" y1="${scale.y(previous).toFixed(1)}" x2="${x(index).toFixed(1)}" y2="${scale.y(current).toFixed(1)}" stroke="${item.color}" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="5 4"/>`;
  };
  const labels = data.map((row, index) => String(row.month || '').endsWith('-01-01') ? `<text x="${x(index)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${String(row.month).slice(0, 4)}</text>` : '').join('');
  const yearGuides = data.map((row, index) => String(row.month || '').endsWith('-01-01') ? `<line x1="${x(index)}" x2="${x(index)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>` : '').join('');
  const dotsFor = (item, scale) => data.map((row, index) => {
    const value = toCreditStressNumber(row[item.key]);
    if (!Number.isFinite(value)) return '';
    const detail = `${row.month}\n${item.label}: ${value.toFixed(item.digits)}${item.suffix}${row.is_latest ? ' (잠정치)' : ''}`;
    const latestMarker = row.is_latest ? ` fill-opacity="0.25" stroke="${item.color}" stroke-width="1.5"` : '';
    return `<circle cx="${x(index)}" cy="${scale.y(value)}" r="3.5" fill="${item.color}"${latestMarker} tabindex="0"><title>${detail}</title></circle>`;
  }).join('');
  const ticksFor = (scale, formatter, color, axisX, withGrid = false) => Array.from({ length: 5 }, (_, index) => scale.upper - ((scale.upper - scale.lower) * index) / 4).map((value, index) => `${withGrid ? `<line x1="${padding.left}" x2="${width - padding.right}" y1="${scale.y(value)}" y2="${scale.y(value)}" stroke="#dbe3ed"${index === 0 || index === 4 ? '' : ' stroke-dasharray="3 4"'}/>` : ''}<text x="${axisX}" y="${scale.y(value) + 3}"${axisX === padding.left - 9 ? ' text-anchor="end"' : ''} fill="${color}" font-size="10">${formatter(value)}</text>`).join('');
  const bankruptcyAxisX = width - 52;
  const axes = `<line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${bankruptcyAxisX}" x2="${bankruptcyAxisX}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>`;
  const legend = series.map((item) => `<span class="inline-flex items-center gap-2"><i class="h-2.5 w-2.5 rounded-full" style="background:${item.color}"></i>${item.label}</span>`).join('');
  chart.innerHTML = `<div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="미국 신용 위험 장기 추이">${axes}${ticksFor(highYieldScale, (value) => value.toFixed(1), highYield.color, padding.left - 9, true)}${yearGuides}${ticksFor(bankruptcyScale, (value) => Math.round(value).toLocaleString('en-US'), bankruptcy.color, bankruptcyAxisX + 8)}<path d="${pathFor(highYield, highYieldScale, false)}" fill="none" stroke="${highYield.color}" stroke-width="2.5" stroke-linecap="round"/><path d="${pathFor(conditions, conditionsScale, false)}" fill="none" stroke="${conditions.color}" stroke-width="2.5" stroke-linecap="round"/><path d="${pathFor(bankruptcy, bankruptcyScale)}" fill="none" stroke="${bankruptcy.color}" stroke-width="2.5" stroke-linecap="round"/>${latestSegmentFor(highYield, highYieldScale)}${latestSegmentFor(conditions, conditionsScale)}${dotsFor(highYield, highYieldScale)}${dotsFor(bankruptcy, bankruptcyScale)}${labels}</svg></div><div class="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-400">${legend}</div>`;
}

async function loadCreditStressComponentsDashboard() {
  const chart = document.getElementById('credit-stress-components-chart');
  if (!chart || !supabaseClient) return;
  try {
    const [monthlyResponse, latestResponse] = await Promise.all([
      supabaseClient.from('us_credit_stress_monthly')
        .select('month,high_yield_oas_pct,financial_conditions_credit_index,business_bankruptcy_filings')
        .order('month', { ascending: false })
        .limit(CREDIT_STRESS_HISTORY_MONTHS + 2),
      supabaseClient.from('us_credit_stress_latest')
        .select('as_of,high_yield_oas_pct,financial_conditions_credit_index')
        .eq('singleton', true)
        .maybeSingle(),
    ]);
    if (monthlyResponse.error) throw monthlyResponse.error;
    if (latestResponse.error) throw latestResponse.error;
    const rows = monthlyResponse.data || [];
    const latest = latestResponse.data;
    const latestDate = latest?.as_of ? String(latest.as_of) : null;
    if (latestDate && !rows.some((row) => row.month === latestDate)) {
      rows.push({ ...latest, month: latestDate, business_bankruptcy_filings: null, is_latest: true });
    }
    renderCreditStressComponents(rows);
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

  const hashByView = { overview: '#news', credit: '#credit', korea: '#korea-stress', em: '#em-msi' };
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
    if (updateHash) {
      window.scrollTo(0, 0);
      if (location.hash !== hashByView[selectedView]) location.hash = hashByView[selectedView];
    }
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
