(() => {
'use strict';

// 뉴스 흐름과 시장 스트레스 시각화 전용 모듈입니다.
// 서버에서 저장된 데이터를 읽고 차트 DOM을 만드는 책임만 가지며,
// 추적 항목 CRUD와 드래그 상태에는 접근하지 않습니다.
const { escapeHtml } = window.MacroWatchFrontend;
const { monotoneSeriesPath, monotoneStyledSegments, seriesStyles, legendItem } = window.MacroWatchAnalysisChart;
const supabaseClient = window.macroWatchSupabase
  || window.MacroWatchFrontend.createSupabaseClient();



const NEWS_SENTIMENT_HISTORY_DAYS = 60;
const CREDIT_STRESS_HISTORY_MONTHS = 36;
const CREDIT_STRESS_CHART_HEIGHT = 375;
const STRESS_HISTORY_QUERY_LIMIT = 5000;
const STRESS_RANGE_DEFAULT_YEARS = '2';
let usStressRangeYears = STRESS_RANGE_DEFAULT_YEARS;
let koreaStressRangeYears = STRESS_RANGE_DEFAULT_YEARS;
let emStressRangeYears = STRESS_RANGE_DEFAULT_YEARS;


function bindStressRangeControls(selector, attribute, getRange, setRange, reload) {
  document.querySelectorAll(`${selector} [${attribute}]`).forEach((button) => {
    button.addEventListener('click', () => {
      const range = button.getAttribute(attribute);
      if (!range || range === getRange()) return;
      setRange(range);
      document.querySelectorAll(`${selector} [${attribute}]`).forEach((item) => {
        item.classList.toggle('is-active', item.getAttribute(attribute) === range);
      });
      reload();
    });
  });
}
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
    barWidthClass: 'news-sentiment-bar--compact min-w-6 max-w-6',
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
let sectorFlowRows = [];
let sectorFlowMobileLayout = null;

// ===== 뉴스 흐름 분석 모듈 =====
// 일별 집계 데이터 조회, 긍정·부정 비율 계산, 기간별 막대 렌더링을 담당한다.
// 기사 분류와 저장은 서버에서 수행하므로 이 구역은 읽기와 화면 표시만 맡는다.
function normalizeDecisiveNewsKeywords(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((keyword) => String(keyword).trim()).filter(Boolean))].slice(0, 8);
}

function renderDecisiveNewsKeywords(container, values) {
  if (!container) return;
  container.innerHTML = values.length
    ? values.map((keyword) => `<span class="decisive-news-keyword">#${escapeHtml(keyword)}</span>`).join('')
    : '<span class="decisive-news-keyword-empty">아직 집계된 키워드가 없습니다.</span>';
}

function aggregateWeeklyDecisiveNews(rows, now = new Date()) {
  // 결정적 뉴스는 누락 방지를 위해 실제 수집·분석이 완료된 article_date 주차에 포함합니다.
  // 뉴스 흐름 그래프의 표시 날짜 보정과 달리 여기서는 하루를 빼지 않습니다.
  const kstNow = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  const kstToday = Date.UTC(kstNow.getUTCFullYear(), kstNow.getUTCMonth(), kstNow.getUTCDate());
  const mondayOffset = (new Date(kstToday).getUTCDay() + 6) % 7;
  const weekStart = kstToday - mondayOffset * 24 * 60 * 60 * 1000;
  const weekEnd = weekStart + 7 * 24 * 60 * 60 * 1000;
  const weeklyRows = rows.filter((row) => {
    const storedDate = Date.parse(`${String(row.article_date || '')}T00:00:00Z`);
    if (!Number.isFinite(storedDate)) return false;
    return storedDate >= weekStart && storedDate < weekEnd;
  });
  return {
    count: weeklyRows.reduce((sum, row) => sum + Number(row.decisive_news_count || 0), 0),
    keywords: normalizeDecisiveNewsKeywords(weeklyRows.flatMap((row) => row.decisive_news_keywords || [])),
  };
}

function renderExtremeNewsSignals(rows) {
  const decisive = document.getElementById('decisive-news-count');
  const keywords = document.getElementById('decisive-news-keywords');
  if (!decisive) return;
  const weekly = aggregateWeeklyDecisiveNews(rows);
  decisive.textContent = `${weekly.count}건`;
  renderDecisiveNewsKeywords(keywords, weekly.keywords);
  document.querySelectorAll('#news-extreme-signals [data-extreme-signal-status]').forEach((element) => { element.textContent = '(월요일 자정 초기화)'; });
}

function formatNewsDate(value) {
  const date = new Date(`${String(value || '')}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return '—';
  date.setUTCDate(date.getUTCDate() - 1);
  return `${date.getUTCMonth() + 1}/${date.getUTCDate()}`;
}

function renderSentimentSegment(percent, colorClass, showLabel) {
  if (!percent) return '';
  const label = showLabel && percent >= 16 ? `<span class="news-sentiment-segment-value text-[9px] font-bold text-white/90">${Math.round(percent)}%</span>` : '';
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
  const date = view.showDates ? `<span class="news-sentiment-row-date w-10 shrink-0 text-right text-xs font-semibold text-slate-600">${formatNewsDate(item.article_date)}</span>` : '';
  return `<div class="news-sentiment-row group flex w-full items-center gap-3"${title ? ` title="${title}"` : ''}>${date}<div class="news-sentiment-horizontal-bar flex h-12 min-w-0 flex-1 overflow-hidden rounded-lg bg-slate-200/80 ring-1 ring-inset ring-slate-300 shadow-sm">${bar}</div></div>`;
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
    chart.innerHTML = '<div class="analysis-empty-state-light col-span-full flex min-h-40 items-center justify-center rounded-xl border border-dashed border-slate-800 bg-slate-950/30 p-5 text-sm text-slate-500">다음 뉴스 분석 후 최근 3일 추이가 표시됩니다.</div>';
    return;
  }

  const view = NEWS_SENTIMENT_VIEWS[newsSentimentView];
  const legend = '<div class="news-sentiment-legend"><span><i class="bg-red-900"></i>긍정</span><span><i class="bg-blue-900"></i>부정</span></div>';
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
      ? '<button type="button" data-news-sentiment-view="expanded" class="news-sentiment-view-button"><i class="fa-solid fa-chart-column" aria-hidden="true"></i><span>더보기</span></button>'
      : '',
    newsSentimentView === 'expanded' && data.length > NEWS_SENTIMENT_VIEWS.expanded.days
      ? '<button type="button" data-news-sentiment-view="all" class="news-sentiment-view-button"><i class="fa-solid fa-clock-rotate-left" aria-hidden="true"></i><span>이전 30일 더 보기</span></button>'
      : '',
    newsSentimentView !== 'recent'
      ? '<button type="button" data-news-sentiment-view="recent" class="news-sentiment-view-button news-sentiment-view-button--back"><i class="fa-solid fa-arrow-left" aria-hidden="true"></i><span>돌아가기</span></button>'
      : '',
  ].join('');
  const graphClass = view.layout === 'horizontal'
    ? 'news-sentiment-graph--recent flex h-60 min-w-0 flex-col justify-center gap-5 rounded-xl border border-slate-200 bg-slate-50 px-4 py-6'
    // 30일 화면은 완성 시점의 30칸 간격을 먼저 확보하고 왼쪽부터 하루씩 채운다.
    : `${newsSentimentView === 'expanded' ? 'news-sentiment-graph--expanded' : 'flex'} h-60 min-w-0 items-end justify-start ${view.gapClass} overflow-x-auto rounded-xl border border-slate-200 bg-slate-50 px-4 py-4`;
  const graphId = newsSentimentView === 'all' ? ' id="news-sentiment-history-scroll"' : '';
  // 그래프 아래 한 줄에서 범례와 기간 전환을 양쪽에 배치해 차트 영역을 넓게 사용한다.
  chart.innerHTML = `<div${graphId} class="news-sentiment-graph ${graphClass}">${bars}</div><div class="news-sentiment-toolbar">${legend}<div class="news-sentiment-controls">${controls}</div></div>`;
  const shouldFocusLatest = newsSentimentView === 'all'
    || (newsSentimentView === 'expanded' && window.matchMedia('(max-width: 1023px)').matches);
  if (shouldFocusLatest) {
    const historyChart = chart.querySelector('.news-sentiment-graph');
    if (historyChart) {
      window.requestAnimationFrame(() => {
        historyChart.scrollLeft = historyChart.scrollWidth - historyChart.clientWidth;
      });
    }
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
      .select('article_date,positive_count,negative_count,neutral_count,uncertain_count,decisive_news_count,decisive_news_keywords')
      .order('article_date', { ascending: false })
      .limit(NEWS_SENTIMENT_HISTORY_DAYS);
    if (error) throw error;
    newsSentimentRows = data || [];
    newsSentimentView = 'recent';
    renderExtremeNewsSignals(newsSentimentRows);
    renderNewsSentiment(newsSentimentRows);
  } catch (error) {
    chart.innerHTML = '<div class="analysis-empty-state-light col-span-full flex min-h-40 items-center justify-center rounded-xl border border-dashed border-slate-800 bg-slate-950/30 p-5 text-sm text-slate-500">잠시 후 다시 시도해 주세요.</div>';
  }
}

// ===== 미국 시장 스트레스 모듈 =====
// 월간·주간 스트레스 데이터의 축 계산과 본지표·보조지표 렌더링을 담당한다.
// 지수 산식과 원천 데이터 수집은 Python 파이프라인에서 수행한다.
function createSvgElement(name, attributes) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
  return element;
}

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
  const weeklyLegend = document.querySelector('[data-us-stress-legend]');
  if (weeklyLegend) weeklyLegend.style.display = weeklyRows.length ? '' : 'none';
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
  const width = window.MacroWatchAnalysisChart.historyWidth(data, 'month', usStressRangeYears);
  const height = CREDIT_STRESS_CHART_HEIGHT;
  const padding = { top: 20, right: 52, bottom: 32, left: 52 };
  const scores = data.map((row) => Number(row.stress_index));
  const sp500Values = data.map((row) => toCreditStressNumber(row.sp500_month_end_close)).filter(Number.isFinite);
  const hasSp500 = sp500Values.length > 1;
  const { min: axisMinimum, max: axisMaximum } = window.MacroWatchAnalysisChart.axisDomain(scores, { minimumSpan: 1 });
  const axisRange = axisMaximum - axisMinimum || 1;
  const gridStep = [0.5, 1, 2, 5, 10, 20, 50, 100].find((step) => step >= axisRange / 4) || 100;
  const sp500Domain = hasSp500 ? window.MacroWatchAnalysisChart.axisDomain(sp500Values, { minimumSpan: 1 }) : { min: 0, max: 1 };
  const sp500AxisMinimum = sp500Domain.min;
  const sp500AxisMaximum = sp500Domain.max;
  const sp500AxisRange = sp500AxisMaximum - sp500AxisMinimum || 1;
  const sp500Step = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000].find((step) => step >= sp500AxisRange / 4) || 5000;
  const x = (index) => padding.left + ((width - padding.left - padding.right) * index) / Math.max(1, data.length - 1);
  const y = (score) => padding.top + ((height - padding.top - padding.bottom) * (axisMaximum - score)) / axisRange;
  const sp500Y = (value) => Number.isFinite(value) ? padding.top + ((height - padding.top - padding.bottom) * (sp500AxisMaximum - value)) / sp500AxisRange : NaN;
  const labels = data.map((row, index) => {
    const month = String(row.month || '');
    if (!month.endsWith('-01') || (index !== 0 && !month.endsWith('-01-01'))) return '';
    return `<text x="${x(index)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${month.slice(0, 4)}</text>`;
  }).join('');
  const lines = monotoneStyledSegments(
    data, (_, index) => x(index), (row) => y(Number(row.stress_index)),
    (previous, row) => ({ stroke: '#b7791f', width: 2.75, dash: row.is_provisional || previous.is_provisional ? '5 5' : '' }),
  );
  const dots = data.map((row, index) => {
    const provisional = Boolean(row.is_provisional);
    const detail = `${row.month}\nUS-MSI: ${Number(row.stress_index).toFixed(1)}${provisional ? ' (잠정치)' : ' (확정치)'}`;
    return `<circle cx="${x(index)}" cy="${y(Number(row.stress_index))}" r="3.75" fill="#b7791f"${provisional ? ' fill-opacity="0.35" stroke="#b7791f" stroke-width="1.5"' : ''} tabindex="0"><title>${detail}</title></circle>`;
  }).join('');
  const sp500Lines = `<path d="${monotoneSeriesPath(data, (_, index) => x(index), (row) => sp500Y(toCreditStressNumber(row.sp500_month_end_close)))}" fill="none" stroke="#6b7280" stroke-width="2.25" stroke-linecap="round"/>`;
  const sp500Dots = data.map((row, index) => {
    const value = toCreditStressNumber(row.sp500_month_end_close);
    if (!Number.isFinite(value)) return '';
    return `<circle cx="${x(index)}" cy="${sp500Y(value)}" r="3.25" fill="#6b7280" tabindex="0"><title>${row.month}\nS&P 500 월말 종가: ${value.toLocaleString('en-US', { maximumFractionDigits: 2 })}</title></circle>`;
  }).join('');
  const sp500Axis = hasSp500 ? Array.from(
    { length: Math.round(sp500AxisRange / sp500Step) + 1 },
    (_, index) => sp500AxisMinimum + index * sp500Step,
  ).map((value) => `<text x="${width - padding.right + 9}" y="${sp500Y(value) + 3}" fill="#6b7280" font-size="10">${value.toLocaleString('en-US', { maximumFractionDigits: 0 })}</text>`).join('') : '';
  const correlationPairs = data
    .map((row) => [Number(row.stress_index), toCreditStressNumber(row.sp500_month_end_close)])
    .filter(([stress, sp500]) => Number.isFinite(stress) && Number.isFinite(sp500));
  const correlation = calculateCorrelation(correlationPairs);
  const grid = Array.from({ length: Math.round(axisRange / gridStep) + 1 }, (_, index) => axisMinimum + index * gridStep)
    .map((score) => `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(score)}" y2="${y(score)}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text x="${padding.left - 9}" y="${y(score) + 3}" text-anchor="end" fill="#64748b" font-size="10">${Number.isInteger(score) ? score : score.toFixed(1)}</text>`).join('');
  chart.innerHTML = `<div class="rounded-xl border border-slate-200 bg-slate-50 p-3"><svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="미국 시장 스트레스 지수와 S&P 500 월말 종가 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${sp500Axis}${lines}${sp500Lines}${dots}${sp500Dots}${labels}</svg></div><div class="mt-4 flex flex-wrap items-center justify-between gap-x-5 gap-y-2 text-xs text-slate-400"><div class="flex flex-wrap gap-x-5 gap-y-2">${legendItem('US-MSI', { stroke: '#b7791f', width: 2.75 })}${legendItem('US-MSI 잠정치', { stroke: '#b7791f', width: 2.75, dash: '5 5' })}${hasSp500 ? legendItem('S&P 500 월말 종가', { stroke: '#6b7280', width: 2.25 }) : ''}</div></div>${correlation == null ? '' : `<p class="mt-2 text-right text-[11px] text-slate-500">US-MSI·S&P 500 동일 월 상관계수: r = ${correlation.toFixed(2)}</p>`}`;
  // US-MSI는 선택 기간 전체의 고정 축으로 해석합니다. 스크롤은 범위 탐색만 담당하며,
  // 현재 보이는 구간마다 선 좌표를 다시 축척하지 않습니다.
  window.MacroWatchAnalysisChart.scrollableSvg(chart.querySelector('svg'), width);
}

function renderMarketStressAndTensionChart(weeklyRows) {
  const chart = document.getElementById('credit-stress-chart');
  const weekly = [...weeklyRows].filter((row) => Number.isFinite(Number(row.tension_index))).sort((a, b) => String(a.week).localeCompare(String(b.week)));
  if (!chart || !weekly.length) return;
  const width = window.MacroWatchAnalysisChart.historyWidth(weekly, 'week', usStressRangeYears), height = CREDIT_STRESS_CHART_HEIGHT, padding = { top: 20, right: 58, bottom: 32, left: 52 };
  const dates = weekly.map((row) => new Date(row.week).getTime());
  const start = Math.min(...dates), end = Math.max(...dates), x = (value) => padding.left + ((new Date(value).getTime() - start) / Math.max(1, end - start)) * (width - padding.left - padding.right);
  const values = weekly.map((row) => Number(row.tension_index)), minimum = Math.min(...values), maximum = Math.max(...values), range = Math.max(maximum - minimum, 1), lower = minimum - range * .1, upper = maximum + range * .1, y = (value) => padding.top + ((height - padding.top - padding.bottom) * (upper - value)) / (upper - lower);
  const sp500Values = weekly.map((row) => toCreditStressNumber(row.sp500_friday_close)).filter(Number.isFinite);
  const hasSp500 = sp500Values.length > 1;
  const sp500Minimum = hasSp500 ? Math.min(...sp500Values) : 0;
  const sp500Maximum = hasSp500 ? Math.max(...sp500Values) : 1;
  const sp500Range = Math.max(sp500Maximum - sp500Minimum, Math.max(sp500Maximum * 0.1, 1));
  const sp500Step = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000].find((step) => step >= sp500Range / 4) || 5000;
  const sp500Lower = hasSp500 ? Math.max(0, Math.floor((sp500Minimum - sp500Range * .1) / sp500Step) * sp500Step) : 0;
  const sp500Upper = hasSp500 ? Math.ceil((sp500Maximum + sp500Range * .1) / sp500Step) * sp500Step : 1;
  const sp500Y = (value) => Number.isFinite(value) ? padding.top + ((height - padding.top - padding.bottom) * (sp500Upper - value)) / Math.max(1, sp500Upper - sp500Lower) : NaN;
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
  const sp500Lines = `<path d="${monotoneSeriesPath(weekly, (row) => x(row.week), (row) => sp500Y(toCreditStressNumber(row.sp500_friday_close)))}" fill="none" stroke="#6b7280" stroke-width="2" stroke-linecap="round"/>`;
  const weeklyPaths = monotoneStyledSegments(
    weekly, (row) => x(row.week), (row) => y(Number(row.tension_index)),
    (previous, row) => {
      const provisional = Boolean(previous.is_provisional || row.is_provisional);
      return provisional ? seriesStyles.stressProvisional : seriesStyles.stress;
    },
  );
  chart.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="미국 주간 시장 스트레스 지수와 S&P 500 주간 종가 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${yearGuides}${sp500Axis}${sp500Lines}${weeklyPaths}${years}</svg>`;
  window.MacroWatchAnalysisChart.scrollableSvg(chart.querySelector('svg'), width);
  const svg = chart.querySelector('svg');
  if (!svg) return;
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
    const sampleWindow = secondaryChanges.slice(Math.max(0, index - 3), index + 1);
    return [row.month, sampleWindow.length === 4 ? sampleWindow.reduce((sum, item) => sum + item.value, 0) / 4 : null];
  }));
  const data = changes.map((row, index) => {
    const sampleWindow = changes.slice(Math.max(0, index - 3), index + 1);
    return {
      ...row,
      average: sampleWindow.length === 4
        ? sampleWindow.reduce((sum, item) => sum + item.value, 0) / 4
        : null,
      secondaryAverage: secondaryAverages.get(row.month) ?? null,
    };
  });
  if (!data.length) {
    chart.innerHTML = `<div class="flex min-h-40 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">${emptyMessage}</div>`;
    return;
  }
  const width = chartId === 'credit-stress-momentum-chart' ? window.MacroWatchAnalysisChart.historyWidth(rows, 'month', usStressRangeYears) : 920;
  const height = 190;
  const padding = { top: 18, right: 52, bottom: 32, left: 52 };
  const momentumValues = data.flatMap((row) => [row.value, row.average, row.secondaryAverage].filter(Number.isFinite));
  const { max: axisMaximum } = window.MacroWatchAnalysisChart.axisDomain(momentumValues, { symmetric: true, minimumSpan: .01 });
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
  const lines = showChanges ? `<path d="${monotoneSeriesPath(data, (row) => x(row.month), (row) => y(row.value))}" fill="none" stroke="${lineColor}" stroke-width="1.75" stroke-linecap="round"/>` : '';
  const averageLines = `<path d="${monotoneSeriesPath(data, (row) => x(row.month), (row) => y(row.average))}" fill="none" stroke="${averageColor}" stroke-width="3" stroke-linecap="round"/>`;
  const secondaryAverageLines = secondaryAverageColor ? `<path d="${monotoneSeriesPath(data, (row) => x(row.month), (row) => y(row.secondaryAverage))}" fill="none" stroke="${secondaryAverageColor}" stroke-width="2.5" stroke-opacity="0.48" stroke-linecap="round"/>` : '';
  const yearGuides = data.filter((row, index) => index > 0 && String(row.month).slice(0, 4) !== String(data[index - 1].month).slice(0, 4)).map((row) => `<line x1="${x(row.month)}" x2="${x(row.month)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>`).join('');
  chart.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="${ariaLabel}"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${yearGuides}${lines}${secondaryAverageLines}${averageLines}</svg>`;
  if (chartId === 'credit-stress-momentum-chart') window.MacroWatchAnalysisChart.scrollableSvg(chart.querySelector('svg'), width);
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
    .limit(STRESS_HISTORY_QUERY_LIMIT);
  if (weeklyResponse.error) return;
  const selectedWeeklyRows = weeklyResponse.data || [];
  renderMarketStressDashboard(monthlyRows, selectedWeeklyRows);
  const weeklyRows = selectedWeeklyRows.map((row) => ({ ...row, month: row.week }));
  renderCreditConditionsMomentum(weeklyRows);
}

async function loadMarketStressDashboard() {
  const chart = document.getElementById('credit-stress-chart');
  if (!chart || !supabaseClient) return;
  try {
    const { data, error } = await supabaseClient.from('us_market_stress_index_monthly')
      .select('month,stress_index,is_provisional')
      .order('month', { ascending: false })
      .limit(STRESS_HISTORY_QUERY_LIMIT);
    if (error) throw error;
    const selectedRows = data || [];
    renderMarketStressDashboard(selectedRows);
    loadMarketTension(selectedRows);
  } catch (error) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">시장 스트레스 지수를 불러오지 못했습니다.</div>';
  }
}

// ===== 이머징 시장 스트레스 모듈 =====
// 이머징 지수와 비교 자산의 공통 주간 시계열을 렌더링한다.
function renderEmStressDashboard(rows) {
  const chart = document.getElementById('em-stress-chart');
  const weekly = [...rows]
    .filter((row) => Number.isFinite(Number(row.stress_index)))
    .sort((a, b) => String(a.week).localeCompare(String(b.week)));
  if (!chart || !weekly.length) return;
  const width = window.MacroWatchAnalysisChart.historyWidth(weekly, 'week', emStressRangeYears), height = CREDIT_STRESS_CHART_HEIGHT, padding = { top: 20, right: 52, bottom: 32, left: 52 };
  const dates = weekly.map((row) => new Date(row.week).getTime());
  const start = Math.min(...dates), end = Math.max(...dates);
  const x = (value) => padding.left + ((new Date(value).getTime() - start) / Math.max(1, end - start)) * (width - padding.left - padding.right);
  const values = weekly.map((row) => Number(row.stress_index));
  const { min: lower, max: upper } = window.MacroWatchAnalysisChart.axisDomain(values, { minimumSpan: 1 });
  const y = (value) => padding.top + ((height - padding.top - padding.bottom) * (upper - value)) / Math.max(1, upper - lower);
  const eemValues = weekly.map((row) => Number(row.eem_weekly_close)).filter(Number.isFinite);
  const hasEem = eemValues.length > 1;
  const eemDomain = hasEem ? window.MacroWatchAnalysisChart.axisDomain(eemValues, { minimumSpan: 1 }) : { min: 0, max: 1 };
  const eemLower = eemDomain.min, eemUpper = eemDomain.max;
  const eemY = (value) => padding.top + ((height - padding.top - padding.bottom) * (eemUpper - value)) / Math.max(1, eemUpper - eemLower);
  const yearRows = weekly.filter((row, index) => index === 0 || String(row.week).slice(0, 4) !== String(weekly[index - 1].week).slice(0, 4));
  const yearGuides = yearRows.slice(1).map((row) => `<line x1="${x(row.week)}" x2="${x(row.week)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>`).join('');
  const years = yearRows.slice(1).map((row) => `<text x="${x(row.week)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${String(row.week).slice(0, 4)}</text>`).join('');
  const grid = Array.from({ length: 5 }, (_, index) => {
    const value = upper - (upper - lower) * index / 4;
    return `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(value)}" y2="${y(value)}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text x="${padding.left - 9}" y="${y(value) + 3}" text-anchor="end" fill="#64748b" font-size="10">${value.toFixed(1)}</text>`;
  }).join('');
  const eem = hasEem ? `<path d="${monotoneSeriesPath(weekly, (row) => x(row.week), (row) => eemY(Number(row.eem_weekly_close)))}" fill="none" stroke="#6b7280" stroke-width="2" stroke-linecap="round"/>` : '';
  const eemLabels = hasEem ? [eemLower, (eemLower + eemUpper) / 2, eemUpper].map((value) => `<text x="${width - padding.right + 8}" y="${eemY(value) + 3}" fill="#6b7280" font-size="10">${value.toFixed(1)}</text>`).join('') : '';
  const paths = monotoneStyledSegments(
    weekly, (row) => x(row.week), (row) => y(Number(row.stress_index)),
    (previous, row) => {
      const provisional = Boolean(previous.is_provisional || row.is_provisional);
      return provisional ? seriesStyles.stressProvisional : seriesStyles.stress;
    },
  );
  chart.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="이머징 시장 스트레스 지수와 EEM 주간 종가 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${yearGuides}${eem}${paths}${eemLabels}${years}</svg>`;
  window.MacroWatchAnalysisChart.scrollableSvg(chart.querySelector('svg'), width, 920, { top: padding.top, bottom: height - padding.bottom, axes: [
    { points: weekly.map(row => ({ x: x(row.week), value: toCreditStressNumber(row.stress_index) })), y, selector: 'path[stroke="#00838c"],path[stroke="#d97706"]' },
    { points: weekly.map(row => ({ x: x(row.week), value: toCreditStressNumber(row.eem_weekly_close) })), y: eemY, side: 'right', selector: 'path[stroke="#6b7280"]' },
  ] });

  const attachVerticalGuide = ({ host, source, showLabels }) => {
    const svg = host?.querySelector('svg');
    if (!svg) return;
    const guide = createSvgElement('line', { y1: padding.top, y2: height - padding.bottom, stroke: '#94a3b8', 'stroke-width': .75, 'stroke-dasharray': '3 4', 'pointer-events': 'none', visibility: 'hidden' });
    const valueLabel = showLabels ? createSvgElement('text', { 'text-anchor': 'middle', fill: '#334155', 'font-size': 11, 'font-weight': 700, stroke: '#f8fafc', 'stroke-width': 4, 'paint-order': 'stroke', 'pointer-events': 'none', visibility: 'hidden' }) : null;
    const periodLabel = showLabels ? createSvgElement('text', { 'text-anchor': 'middle', fill: '#64748b', 'font-size': 10, 'pointer-events': 'none', visibility: 'hidden' }) : null;
    svg.append(guide);
    if (valueLabel && periodLabel) svg.append(valueLabel, periodLabel);
    const show = (week) => {
      const nearest = weekly.reduce((closest, row) => Math.abs(x(row.week) - x(week)) < Math.abs(x(closest.week) - x(week)) ? row : closest);
      const pointX = x(nearest.week);
      guide.setAttribute('x1', pointX); guide.setAttribute('x2', pointX); guide.setAttribute('visibility', 'visible');
      if (valueLabel && periodLabel) {
        const [, month, day] = String(nearest.week).split('-').map(Number);
        valueLabel.setAttribute('x', pointX); valueLabel.setAttribute('y', padding.top + 11); valueLabel.setAttribute('visibility', 'visible');
        valueLabel.textContent = Number(nearest.stress_index).toFixed(2);
        periodLabel.setAttribute('x', pointX); periodLabel.setAttribute('y', height - padding.bottom + 12); periodLabel.setAttribute('visibility', 'visible');
        periodLabel.textContent = `${month}월 ${Math.ceil(day / 7)}주`;
      }
    };
    const clear = () => [guide, valueLabel, periodLabel].filter(Boolean).forEach((element) => element.setAttribute('visibility', 'hidden'));
    const onShared = ({ detail }) => {
      if (detail.source === source) return;
      if (detail.active) show(detail.week); else clear();
    };
    if (host._emStressGuideListener) window.removeEventListener('macrowatch:em-stress-hover', host._emStressGuideListener);
    host._emStressGuideListener = onShared;
    window.addEventListener('macrowatch:em-stress-hover', onShared);
    svg.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = ((event.clientX - bounds.left) / bounds.width) * width;
      const nearest = weekly.reduce((closest, row) => Math.abs(x(row.week) - pointerX) < Math.abs(x(closest.week) - pointerX) ? row : closest);
      show(nearest.week);
      window.dispatchEvent(new CustomEvent('macrowatch:em-stress-hover', { detail: { active: true, source, week: nearest.week } }));
    });
    svg.addEventListener('pointerleave', () => {
      clear();
      window.dispatchEvent(new CustomEvent('macrowatch:em-stress-hover', { detail: { active: false, source } }));
    });
  };
  attachVerticalGuide({ host: chart, source: 'em-main', showLabels: true });
}

async function loadEmStressDashboard() {
  const chart = document.getElementById('em-stress-chart');
  if (!chart || !supabaseClient) return;
  try {
    const { data, error } = await supabaseClient.from('em_market_stress_weekly')
      .select('week,stress_index,eem_weekly_close,is_provisional')
      .order('week', { ascending: false }).limit(STRESS_HISTORY_QUERY_LIMIT);
    if (error) throw error;
    renderEmStressDashboard(data || []);
  } catch (_) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">이머징 시장 스트레스 지수를 불러오지 못했습니다.</div>';
  }
}

// ===== 한국 시장 스트레스 모듈 =====
// K-MSI 월간 본지표, 주간 코스피 비교선, 한국은행 FSI 보조지표를 표시한다.
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
    chart.innerHTML = '<div class="analysis-empty-state-light flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">첫 산출 후 한국 시장 스트레스 지수가 표시됩니다.</div>';
    if (fsiChart) fsiChart.innerHTML = '';
    return;
  }
  const weeklyKospi = weeklyKospiSource;
  const width = window.MacroWatchAnalysisChart.historyWidth(data, 'month', koreaStressRangeYears), height = CREDIT_STRESS_CHART_HEIGHT, padding = { top: 20, right: 58, bottom: 32, left: 52 };
  const dates = [...data.map((row) => new Date(row.month).getTime()), ...weeklyKospi.map((row) => new Date(row.week).getTime())];
  const start = Math.min(...dates), end = Math.max(...dates);
  const x = (month) => padding.left + ((new Date(month).getTime() - start) / Math.max(1, end - start)) * (width - padding.left - padding.right);
  const leftValues = data.map((row) => Number(row.stress_index)).filter(Number.isFinite);
  const { min: lower, max: upper } = window.MacroWatchAnalysisChart.axisDomain(leftValues, { minimumSpan: 1 });
  const y = (value) => padding.top + (height - padding.top - padding.bottom) * (upper - value) / Math.max(1, upper - lower);
  const kospiValues = weeklyKospi.map((row) => Number(row.kospi_close)).filter(Number.isFinite);
  const hasKospi = kospiValues.length > 1;
  const kospiDomain = hasKospi ? window.MacroWatchAnalysisChart.axisDomain(kospiValues, { minimumSpan: 1 }) : { min: 0, max: 1 }, kospiLower = kospiDomain.min, kospiUpper = kospiDomain.max;
  const kospiY = (value) => padding.top + (height - padding.top - padding.bottom) * (kospiUpper - value) / Math.max(1, kospiUpper - kospiLower);
  const grid = Array.from({ length: 5 }, (_, index) => {
    const value = upper - (upper - lower) * index / 4, py = y(value);
    return `<line x1="${padding.left}" x2="${width - padding.right}" y1="${py}" y2="${py}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text x="${padding.left - 9}" y="${py + 3}" text-anchor="end" fill="#64748b" font-size="10">${value.toFixed(1)}</text>`;
  }).join('');
  const years = data.filter((row, index) => index > 0 && String(row.month).slice(0, 4) !== String(data[index - 1].month).slice(0, 4));
  const yearGuides = years.map((row) => `<line x1="${x(row.month)}" x2="${x(row.month)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/><text x="${x(row.month)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${String(row.month).slice(0, 4)}</text>`).join('');
  const stress = monotoneStyledSegments(
    data, (row) => x(row.month), (row) => y(Number(row.stress_index)),
    (previous, row) => {
      const provisional = Boolean(previous.is_provisional || row.is_provisional);
      return provisional ? seriesStyles.stressProvisional : seriesStyles.stress;
    },
  );
  const kospi = hasKospi ? `<path d="${monotoneSeriesPath(weeklyKospi, (row) => x(row.week), (row) => kospiY(Number(row.kospi_close)))}" fill="none" stroke="#6b7280" stroke-width="2" stroke-linecap="round"/>` : '';
  const kospiLabels = hasKospi ? [kospiLower, (kospiLower + kospiUpper) / 2, kospiUpper].map((value) => `<text x="${width - padding.right + 8}" y="${kospiY(value) + 3}" fill="#6b7280" font-size="10">${Math.round(value).toLocaleString('en-US')}</text>`).join('') : '';
  chart.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="한국 시장 스트레스 지수와 코스피 주간 종가 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${yearGuides}${kospi}${stress}${kospiLabels}</svg>`;
  window.MacroWatchAnalysisChart.scrollableSvg(chart.querySelector('svg'), width, 920, { top: padding.top, bottom: height - padding.bottom, axes: [
    { points: data.map(row => ({ x: x(row.month), value: toCreditStressNumber(row.stress_index) })), y, selector: 'path[stroke="#00838c"],path[stroke="#d97706"]' },
    { points: weeklyKospi.map(row => ({ x: x(row.week), value: toCreditStressNumber(row.kospi_close) })), y: kospiY, side: 'right', selector: 'path[stroke="#6b7280"]' },
  ] });

  const attachHover = ({ host, hoverRows, valueKey, chartHeight, chartPadding, source, label, showLabels = true }) => {
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
        period.textContent = `${Number(String(nearest.month).slice(5, 7))}월`;
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
  attachHover({ host: chart, hoverRows: data, valueKey: 'stress_index', chartHeight: height, chartPadding: padding, source: 'korea-main', label: '' });
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
  const fsiYearGuides = fsiRows
    .filter((row, index) => index > 0 && String(row.month).slice(0, 4) !== String(fsiRows[index - 1].month).slice(0, 4))
    .map((row) => `<line x1="${x(row.month)}" x2="${x(row.month)}" y1="${fsiPadding.top}" y2="${fsiHeight - fsiPadding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>`)
    .join('');
  const fsiLine = `<path d="${monotoneSeriesPath(fsiRows, (row) => x(row.month), (row) => fsiY(Number(row.bok_fsi)))}" fill="none" stroke="#6d4b91" stroke-width="2.25" stroke-linecap="round"/>`;
  fsiChart.innerHTML = `<svg class="w-full" style="height:${fsiHeight}px" viewBox="0 0 ${width} ${fsiHeight}" role="img" aria-label="한국은행 금융불안지수 보조지표"><line x1="${fsiPadding.left}" x2="${fsiPadding.left}" y1="${fsiPadding.top}" y2="${fsiHeight - fsiPadding.bottom}" stroke="#b6a8d0"/><line x1="${width - fsiPadding.right}" x2="${width - fsiPadding.right}" y1="${fsiPadding.top}" y2="${fsiHeight - fsiPadding.bottom}" stroke="#b6a8d0"/>${fsiGrid}${fsiYearGuides}${fsiLine}</svg>`;
  window.MacroWatchAnalysisChart.scrollableSvg(fsiChart.querySelector('svg'), width, 920, { top: fsiPadding.top, bottom: fsiHeight - fsiPadding.bottom, axes: [
    { points: fsiRows.map(row => ({ x: x(row.month), value: toCreditStressNumber(row.bok_fsi) })), y: fsiY, selector: 'path[stroke="#6d4b91"]' },
  ] });
  attachHover({ host: fsiChart, hoverRows: fsiRows, valueKey: 'bok_fsi', chartHeight: fsiHeight, chartPadding: fsiPadding, source: 'korea-fsi', label: 'FSI', showLabels: false });
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
        .order('month', { ascending: false }).limit(STRESS_HISTORY_QUERY_LIMIT),
      supabaseClient.from('korea_market_stress_weekly')
        .select('week,kospi_close,corporate_credit_spread,short_term_funding_spread')
        .order('week', { ascending: false }).limit(STRESS_HISTORY_QUERY_LIMIT),
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
    renderKoreaStressChart(
      displayRows,
      weeklyResponse.data || [],
    );
  } catch (error) {
    chart.innerHTML = '<div class="flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">한국 시장 스트레스 데이터를 불러오지 못했습니다.</div>';
  }
}

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

// ===== 미국 신용위험 구성지표 모듈 =====
// 단위가 다른 원천지표를 각자의 축으로 그려 장기 방향을 비교한다.
function renderCreditStressComponents(rows) {
  const chart = document.getElementById('credit-stress-components-chart');
  if (!chart) return;
  if (!rows.length) {
    chart.innerHTML = '<div class="analysis-empty-state-light flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">첫 수집 후 장기 신용위험 추이가 표시됩니다.</div>';
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
  const width = Math.max(680, (chart.clientWidth || 808) - 128, data.length * 48);
  const height = CREDIT_STRESS_CHART_HEIGHT;
  const padding = { top: 48, right: 16, bottom: 32, left: 16 };
  const dates = data.map((row) => new Date(row.month).getTime());
  const firstDate = Math.min(...dates);
  const lastDate = Math.max(...dates);
  const x = (index) => padding.left + ((dates[index] - firstDate) / Math.max(1, lastDate - firstDate)) * (width - padding.left - padding.right);
  const scaleFor = (item, source = data) => {
    const values = source.map((row) => toCreditStressNumber(row[item.key])).filter(Number.isFinite);
    if (!values.length) {
      if (source !== data) return scaleFor(item);
      values.push(0, 1);
    }
    const { min: lower, max: upper } = window.MacroWatchAnalysisChart.axisDomain(values, { minimumSpan: .01 });
    return { lower, upper, y: (value) => padding.top + ((height - padding.top - padding.bottom) * (upper - value)) / (upper - lower) };
  };
  const [highYield, conditions, bankruptcy] = series;
  const highYieldScale = scaleFor(highYield);
  const conditionsScale = scaleFor(conditions);
  const bankruptcyScale = scaleFor(bankruptcy);
  const pathFor = (item, scale, includeLatest = true) => {
    return monotoneSeriesPath(
      data.map((row, index) => ({ ...row, _chartIndex: index }))
        // 최신 빠른 지표 행에는 월간 파산보호 신청치가 없습니다. null을 좌표식에 넘기면
        // JavaScript가 0으로 강제 변환하므로, 값이 있는 행만 해당 시계열에 그립니다.
        .filter((row) => (includeLatest || !row.is_latest) && Number.isFinite(toCreditStressNumber(row[item.key]))),
      (row) => x(row._chartIndex),
      (row) => scale.y(toCreditStressNumber(row[item.key])),
    );
  };
  const latestSegmentFor = (item, scale) => {
    const index = data.findIndex((row) => row.is_latest);
    if (index < 1) return '';
    const previous = toCreditStressNumber(data[index - 1][item.key]);
    const current = toCreditStressNumber(data[index][item.key]);
    if (!Number.isFinite(previous) || !Number.isFinite(current)) return '';
    return `<line data-credit-latest="${item.key}" x1="${x(index - 1).toFixed(1)}" y1="${scale.y(previous).toFixed(1)}" x2="${x(index).toFixed(1)}" y2="${scale.y(current).toFixed(1)}" stroke="${item.color}" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="5 4"/>`;
  };
  const labels = data.map((row, index) => String(row.month || '').endsWith('-01-01') ? `<text x="${x(index)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${String(row.month).slice(0, 4)}</text>` : '').join('');
  const yearGuides = data.map((row, index) => String(row.month || '').endsWith('-01-01') ? `<line x1="${x(index)}" x2="${x(index)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>` : '').join('');
  const dotsFor = (item, scale) => data.map((row, index) => {
    const value = toCreditStressNumber(row[item.key]);
    if (!Number.isFinite(value)) return '';
    const detail = `${row.month}\n${item.label}: ${value.toFixed(item.digits)}${item.suffix}${row.is_latest ? ' (잠정치)' : ''}`;
    const latestMarker = row.is_latest ? ` fill-opacity="0.25" stroke="${item.color}" stroke-width="1.5"` : '';
    return `<circle data-credit-point="${item.key}" data-credit-index="${index}" cx="${x(index)}" cy="${scale.y(value)}" r="3.5" fill="${item.color}"${latestMarker} tabindex="0"><title>${detail}</title></circle>`;
  }).join('');

  const ticksFor = (scale, formatter, color, right = false) => Array.from({ length: 5 }, (_, index) => {
    const value = scale.upper - (scale.upper - scale.lower) * index / 4;
    return `<text x="${right ? 8 : 58}" y="${scale.y(value) + 3}" text-anchor="${right ? 'start' : 'end'}" fill="${color}" font-size="10">${formatter(value)}</text>`;
  }).join('');
  const legend = series.map(item => {
    const latestIndex = data.findIndex(row => row.is_latest);
    const hasLatestSegment = item.key !== 'business_bankruptcy_filings_3m_average' && latestIndex > 0
      && [data[latestIndex - 1], data[latestIndex]].every(row => Number.isFinite(toCreditStressNumber(row[item.key])));
    return legendItem(item.label, { stroke: item.color, width: 2.5 })
      + (hasLatestSegment ? legendItem(`${item.label} 최신값`, { stroke: item.color, width: 2.5, dash: '5 4' }) : '');
  }).join('');
  const grids = Array.from({length:5}, (_, i) => {
    const py = padding.top + (height - padding.top - padding.bottom) * i / 4;
    return `<line x1="${padding.left}" x2="${width-padding.right}" y1="${py}" y2="${py}" class="korea-earnings-grid"/>`;
  }).join('');
  // 곡선 보간과 스크롤 구간별 축 재조정으로 좌표가 플롯 바깥에 생길 수 있으므로,
  // 데이터 선·점만 플롯 사각형 안에서 자릅니다. 축·연도 표기·커서는 그대로 유지합니다.
  const plotClip = `<defs><clipPath id="credit-risk-plot-clip"><rect x="${padding.left}" y="${padding.top}" width="${width - padding.left - padding.right}" height="${height - padding.top - padding.bottom}"/></clipPath></defs>`;
  const plottedSeries = `<g clip-path="url(#credit-risk-plot-clip)"><path data-credit-series="${highYield.key}" d="${pathFor(highYield,highYieldScale,false)}" fill="none" stroke="${highYield.color}" stroke-width="2.5" stroke-linecap="round"/><path data-credit-series="${conditions.key}" d="${pathFor(conditions,conditionsScale,false)}" fill="none" stroke="${conditions.color}" stroke-width="2.5" stroke-linecap="round"/><path data-credit-series="${bankruptcy.key}" d="${pathFor(bankruptcy,bankruptcyScale)}" fill="none" stroke="${bankruptcy.color}" stroke-width="2.5" stroke-linecap="round"/>${latestSegmentFor(highYield,highYieldScale)}${latestSegmentFor(conditions,conditionsScale)}${dotsFor(highYield,highYieldScale)}${dotsFor(bankruptcy,bankruptcyScale)}</g>`;
  chart.innerHTML = `<div class="rounded-xl border border-slate-200 bg-white p-3"><div class="korea-earnings-chart-layout"><svg data-credit-left-axis class="korea-earnings-y-axis" style="height:${height}px" viewBox="0 0 64 ${height}" aria-hidden="true"></svg><div class="korea-earnings-chart-frame" tabindex="0" aria-label="미국 신용위험 전체 이력 가로 스크롤"><svg class="korea-earnings-chart-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="미국 신용 위험 장기 추이">${plotClip}${grids}${yearGuides}${plottedSeries}${labels}<line data-credit-cursor y1="${padding.top}" y2="${height-padding.bottom}" class="korea-earnings-cursor"/><text data-credit-cursor-label class="korea-earnings-cursor-label" text-anchor="middle"></text><text data-credit-cursor-date y="${height-8}" class="korea-earnings-cursor-period" text-anchor="middle"></text></svg></div><svg data-credit-right-axis class="korea-earnings-y-axis" style="height:${height}px" viewBox="0 0 64 ${height}" aria-hidden="true"></svg></div></div><div class="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-slate-400">${legend}</div>`;
  const frame = chart.querySelector('.korea-earnings-chart-frame');
  const svg = frame.querySelector('svg');
  const cursor = chart.querySelector('[data-credit-cursor]');
  const cursorLabel = chart.querySelector('[data-credit-cursor-label]');
  const cursorDate = chart.querySelector('[data-credit-cursor-date]');
  const hideCursor = () => [cursor,cursorLabel,cursorDate].forEach(node=>node.classList.remove('is-visible'));
  frame.addEventListener('pointermove', event => {
    const bounds = svg.getBoundingClientRect();
    const pointerX = (event.clientX - bounds.left) / bounds.width * width;
    const index = data.reduce((best, _, i) => Math.abs(x(i)-pointerX) < Math.abs(x(best)-pointerX) ? i : best, 0);
    const row = data[index], px = x(index);
    const labelX = frame.scrollLeft + frame.clientWidth / 2;
    cursor.setAttribute('x1',px); cursor.setAttribute('x2',px);
    cursorLabel.innerHTML = series.map((item,i) => {
      const value = toCreditStressNumber(row[item.key]);
      return `<tspan x="${labelX}" y="${12+i*13}">${item.label}: ${Number.isFinite(value) ? value.toFixed(item.digits)+item.suffix : '미발표'}</tspan>`;
    }).join('');
    cursorDate.setAttribute('x', Math.max(frame.scrollLeft+40,Math.min(frame.scrollLeft+frame.clientWidth-40,px)));
    cursorDate.textContent = row.month + (row.is_latest ? ' (잠정치)' : '');
    [cursor,cursorLabel,cursorDate].forEach(node=>node.classList.add('is-visible'));
  });
  frame.addEventListener('pointerleave',hideCursor);
  const updateVisibleScale = () => {
    const visibleIndexes = data.map((_, index) => index).filter((index) => x(index) >= frame.scrollLeft && x(index) <= frame.scrollLeft + frame.clientWidth);
    const visible = visibleIndexes.length
      ? data.slice(Math.max(0, visibleIndexes[0] - 1), Math.min(data.length, visibleIndexes.at(-1) + 2))
      : [];
    if (!visible.length) return;
    const scales = series.map(item=>scaleFor(item,visible));
    chart.querySelector('[data-credit-left-axis]').innerHTML = ticksFor(scales[0],v=>v.toFixed(1),highYield.color);
    chart.querySelector('[data-credit-right-axis]').innerHTML = ticksFor(scales[2],v=>Math.round(v).toLocaleString('en-US'),bankruptcy.color,true);
    series.forEach((item,i)=>{
      const scale = scales[i];
      chart.querySelector(`[data-credit-series="${item.key}"]`).setAttribute('d',pathFor(item,scale,item===bankruptcy));
      chart.querySelectorAll(`[data-credit-point="${item.key}"]`).forEach(dot=>{
        dot.setAttribute('cy',scale.y(toCreditStressNumber(data[Number(dot.dataset.creditIndex)][item.key])));
      });
      const segment = chart.querySelector(`[data-credit-latest="${item.key}"]`);
      if (segment) {
        const index = data.findIndex(row=>row.is_latest);
        segment.setAttribute('y1',scale.y(toCreditStressNumber(data[index-1][item.key])));
        segment.setAttribute('y2',scale.y(toCreditStressNumber(data[index][item.key])));
      }
    });
  };
  let pendingFrame = null;
  frame.addEventListener('scroll',()=>{
    hideCursor();
    if (pendingFrame===null) pendingFrame=requestAnimationFrame(()=>{pendingFrame=null;updateVisibleScale();});
  },{passive:true});
  const observer = new ResizeObserver(()=>{
    if (frame.clientWidth > 0) {
      window.MacroWatchAnalysisChart.scrollToLatest(frame);
      updateVisibleScale();
      observer.disconnect();
    }
  });
  observer.observe(frame);
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
    chart.innerHTML = '<div class="analysis-empty-state-light flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">신용위험 데이터를 불러오지 못했습니다.</div>';
  }
}

// ===== 주도섹터 흐름 모듈 =====
// 서버가 계산한 주차별 상위 순위만 읽습니다. 가격 원본과 순위 산식은 Edge Function에 남겨
// 브라우저별 시간대나 부동소수점 차이로 순위가 달라지지 않도록 합니다.
function sectorRankChange(row, showNew) {
  if (showNew && row.is_new) return '<small class="sector-flow-change-new text-teal-500">NEW</small>';
  if (!row.previous_rank) return '';
  const change = Number(row.previous_rank) - Number(row.rank);
  if (change > 0) return `<small class="sector-flow-change-move text-red-500">▲${change}</small>`;
  if (change < 0) return `<small class="sector-flow-change-move text-blue-500">▼${Math.abs(change)}</small>`;
  return '<small class="sector-flow-change-flat text-slate-500">-</small>';
}

function sectorReturn(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const absolute = Math.abs(number);
  const formatted = absolute < 10
    ? number.toFixed(2)
    : absolute < 100
      ? number.toFixed(1)
      : Math.trunc(number).toString();
  return `${number > 0 ? '+' : ''}${formatted}%`;
}

function sectorReturnTone(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number === 0) return 'sector-return-neutral';
  return number > 0 ? 'sector-return-positive' : 'sector-return-negative';
}

function sectorLeadership(value, compact = false) {
  const empty = compact
    ? '<span class="sector-flow-leadership sector-flow-leadership-empty"><small>주도력</small><em>—</em></span>'
    : '<span class="sector-flow-leadership sector-flow-leadership-empty"><span><i style="width:0%"></i></span><em>—</em></span>';
  if (value === null || value === undefined || value === '') {
    return empty;
  }
  const score = Math.max(0, Math.min(100, Math.round(Number(value))));
  if (!Number.isFinite(score)) return empty;
  return compact
    ? `<span class="sector-flow-leadership" title="최근 20거래일 상승장 조건 아래 10주간 초과수익 강도와 지속성"><small>주도력</small><em>${score}</em></span>`
    : `<span class="sector-flow-leadership" title="최근 20거래일 상승장 조건 아래 10주간 초과수익 강도와 지속성"><span><i style="width:${score}%"></i></span><em>${score}</em></span>`;
}

function setSectorWeekHeading(card, week, isLatest) {
  if (!week) return;
  const date = new Date(`${week}T00:00:00Z`);
  const period = card.querySelector('[data-sector-week-period]');
  const label = card.querySelector('[data-sector-week-label]');
  if (period) period.textContent = `${date.getUTCMonth() + 1}월`;
  if (label) label.textContent = isLatest ? '이번 주 섹터별 주간 수익률 순위' : `${Math.floor((date.getUTCDate() - 1) / 7) + 1}주차`;
}

function sectorHoldingsTooltip(etf) {
  const holdings = [...(etf?.market_sector_etf_holdings || [])]
    .sort((a, b) => Number(a.weight_rank) - Number(b.weight_rank))
    .slice(0, 3);
  const sectorName = escapeHtml(etf?.sector_name || '—');
  const items = holdings.length
    ? holdings.map((holding) => `<li><span>${escapeHtml(holding.holding_name)}</span></li>`).join('')
    : '<li class="sector-flow-holdings-empty"><span>미수집</span></li>';
  return `<button type="button" class="sector-flow-sector" aria-expanded="false"><span>${sectorName}</span><span class="sector-flow-holdings" role="tooltip"><b>섹터 대표 종목</b><ol>${items}</ol></span></button>`;
}

function sectorTopHolding(etf) {
  const topHolding = [...(etf?.market_sector_etf_holdings || [])]
    .sort((a, b) => Number(a.weight_rank) - Number(b.weight_rank))[0];
  return `<span class="sector-flow-top-holding">${topHolding ? escapeHtml(topHolding.holding_name) : '미수집'}</span>`;
}

function closeSectorHoldings(trigger) {
  if (!trigger) return;
  trigger.classList.remove('is-open');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.closest('.sector-flow-week')?.classList.remove('sector-flow-week-popover-open');
}

// 구성 종목은 명시적인 클릭으로만 열고, 트리거와 팝업 영역을 벗어나면 즉시 닫습니다.
function initializeSectorHoldingInteractions() {
  const dashboard = document.getElementById('sector-flow-dashboard');
  if (!dashboard || dashboard.dataset.holdingInteractionsReady) return;
  dashboard.dataset.holdingInteractionsReady = 'true';
  dashboard.addEventListener('click', (event) => {
    const trigger = event.target.closest('.sector-flow-sector');
    dashboard.querySelectorAll('.sector-flow-sector.is-open').forEach((item) => {
      if (item !== trigger) closeSectorHoldings(item);
    });
    if (!trigger) return;
    const opening = !trigger.classList.contains('is-open');
    closeSectorHoldings(trigger);
    if (opening) {
      trigger.classList.add('is-open');
      trigger.setAttribute('aria-expanded', 'true');
      trigger.closest('.sector-flow-week')?.classList.add('sector-flow-week-popover-open');
    }
  });
  dashboard.addEventListener('pointerout', (event) => {
    const trigger = event.target.closest('.sector-flow-sector.is-open');
    if (trigger && !trigger.contains(event.relatedTarget)) closeSectorHoldings(trigger);
  });
  dashboard.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeSectorHoldings(event.target.closest('.sector-flow-sector.is-open'));
  });
}

function initializeSectorFlowSwipeIndicator() {
  const preview = document.querySelector('.sector-flow-preview');
  const indicator = document.querySelector('[data-sector-flow-swipe-indicator]');
  const dots = [...(indicator?.querySelectorAll('.sector-flow-swipe-dots i') || [])];
  if (!preview || !indicator || !dots.length) return;
  const update = () => {
    const cards = [...preview.querySelectorAll('.sector-flow-week')];
    const activeIndex = cards.reduce((closest, card, index) => (
      Math.abs(card.offsetLeft - preview.scrollLeft) < Math.abs(cards[closest].offsetLeft - preview.scrollLeft) ? index : closest
    ), 0);
    dots.forEach((dot, index) => dot.classList.toggle('is-active', index === activeIndex));
    indicator.setAttribute('aria-label', `주차 ${activeIndex + 1}/${cards.length}`);
  };
  const positionCurrentWeek = () => {
    if (!window.matchMedia('(max-width: 1023px)').matches || preview.dataset.initialWeekPositioned) {
      update();
      return;
    }
    const current = preview.querySelector('.sector-flow-week-current');
    if (!current || !current.offsetWidth) return;
    const paddingLeft = parseFloat(getComputedStyle(preview).paddingLeft) || 0;
    preview.scrollLeft = Math.max(0, current.offsetLeft - paddingLeft);
    preview.dataset.initialWeekPositioned = 'true';
    update();
  };
  if (!preview.dataset.swipeIndicatorReady) {
    preview.dataset.swipeIndicatorReady = 'true';
    let frame = 0;
    preview.addEventListener('scroll', () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(update);
    }, { passive: true });
    window.addEventListener('resize', positionCurrentWeek);
    window.addEventListener('resize', () => {
      const mobileLayout = window.matchMedia('(max-width: 1023px)').matches;
      if (mobileLayout === sectorFlowMobileLayout) return;
      renderSectorFlow(sectorFlowRows);
    });
  }
  requestAnimationFrame(positionCurrentWeek);
}

function renderSectorFlow(rows) {
  const isMobile = window.matchMedia('(max-width: 1023px)').matches;
  sectorFlowMobileLayout = isMobile;
  const grouped = new Map();
  rows.forEach((row) => {
    const list = grouped.get(row.week_start) || [];
    list.push(row);
    grouped.set(row.week_start, list);
  });
  // 화면에는 최신 5주만 표시하지만, 그 앞의 한 주도 받아 맨 왼쪽 카드의 변동 기준을 보존합니다.
  const weeks = [...grouped.keys()].sort().slice(-6);
  const cards = [...document.querySelectorAll('[data-sector-week-offset]')];
  cards.forEach((card) => {
    const offset = Number(card.dataset.sectorWeekOffset);
    const week = weeks[weeks.length - 1 + offset], isLatestWeek = offset === 0;
    const list = (grouped.get(week) || []).sort((a, b) => Number(a.rank) - Number(b.rank)).slice(0, 6);
    const body = card.querySelector('ol');
    if (!body) return;
    const columns = card.querySelector('.sector-flow-columns');
    if (columns) columns.innerHTML = isLatestWeek
      ? isMobile
        ? '<span>순위</span><span>변동</span><span>섹터 <span class="sector-flow-classification-note">(KRX 업종 구분과 다름)</span></span><span>연속</span>'
        : '<span>순위</span><span>변동</span><span>섹터 <span class="sector-flow-classification-note">(KRX 업종 구분과 다름)</span></span><span>대표종목</span><span>주간 수익률</span><span>4주 누적 수익률</span><span>랭킹 연속 유지</span><span>주도력</span>'
      : '<span>순위</span><span>변동</span><span>섹터</span><span>연속</span>';
    setSectorWeekHeading(card, week, isLatestWeek);
    body.innerHTML = list.length ? list.map((row) => {
      const etf = row.market_sector_etfs;
      const rank = `<b class="sector-flow-rank">${Number(row.rank)}</b><span class="sector-flow-change">${sectorRankChange(row, isLatestWeek)}</span>${sectorHoldingsTooltip(etf)}`;
      const returns = `<div class="sector-flow-returns"><span><small>주간</small><em class="${sectorReturnTone(row.weekly_return_pct)}">${sectorReturn(row.weekly_return_pct)}</em></span><span><small>누적</small><em class="${sectorReturnTone(row.cumulative_return_pct)}">${sectorReturn(row.cumulative_return_pct)}</em></span>${isLatestWeek && isMobile ? sectorLeadership(row.leadership_score, true) : ''}</div>`;
      const streak = `<span class="sector-flow-streak">${Number(row.top10_streak)}${isLatestWeek ? '주차' : '주'}</span>`;
      return `<li>${rank}${isLatestWeek && !isMobile ? `${sectorTopHolding(etf)}${returns}${streak}${sectorLeadership(row.leadership_score)}` : `${streak}${returns}`}</li>`;
    }).join('') : isLatestWeek
      ? isMobile
        ? '<li><b class="sector-flow-rank">—</b><span class="sector-flow-change">—</span><strong>산출 대기</strong><span class="sector-flow-streak">—주차</span><div class="sector-flow-returns"><span><small>주간</small><em>—</em></span><span><small>누적</small><em>—</em></span><span class="sector-flow-leadership sector-flow-leadership-empty"><small>주도력</small><em>—</em></span></div></li>'
        : '<li><b class="sector-flow-rank">—</b><span class="sector-flow-change">—</span><strong>산출 대기</strong><span class="sector-flow-top-holding">—</span><div class="sector-flow-returns"><span><small>주간</small><em>—</em></span><span><small>누적</small><em>—</em></span></div><span class="sector-flow-streak">—주차</span><span class="sector-flow-leadership sector-flow-leadership-empty"><span><i style="width:0%"></i></span><em>—</em></span></li>'
      : '<li><b class="sector-flow-rank">—</b><span class="sector-flow-change">—</span><strong>산출 대기</strong><span class="sector-flow-streak">—주</span><div class="sector-flow-returns"><span><small>주간</small><em>—</em></span><span><small>누적</small><em>—</em></span></div></li>';
  });
  const note = document.getElementById('sector-flow-update-note');
  if (note) note.textContent = '매 영업일 시가·종가 반영';
  initializeSectorFlowSwipeIndicator();
}

async function loadSectorFlowDashboard() {
  if (!document.getElementById('sector-flow-dashboard') || !supabaseClient) return;
  initializeSectorHoldingInteractions();
  initializeSectorFlowSwipeIndicator();
  renderSectorFlow(sectorFlowRows);
  const { data, error } = await supabaseClient.from('market_sector_weekly_rankings')
    .select('week_start,rank,previous_rank,is_new,top10_streak,weekly_return_pct,cumulative_return_pct,leadership_score,price_stage,market_sector_etfs(sector_name,market_sector_etf_holdings(holding_name,weight_pct,weight_rank))')
    // 전체 순위를 6주 보관하므로 현재 등록 규모보다 넉넉하게 읽고 화면에서 주차별 TOP 6만 추린다.
    .order('week_start', { ascending: false }).order('rank', { ascending: true }).limit(1000);
  if (error) return;
  sectorFlowRows = data || [];
  renderSectorFlow(sectorFlowRows);
}

// 기존 대시보드 공개 계산 계약과 초기 로더 등록을 유지합니다.
window.MacroWatchAnalysisChart.initializeLegends();
window.MacroWatchChartUtils = Object.freeze({ aggregateWeeklyDecisiveNews, calculateCorrelation, formatNewsDate });
bindStressRangeControls('[data-us-stress-ranges]', 'data-us-stress-range', () => usStressRangeYears, (value) => { usStressRangeYears = value; }, loadMarketStressDashboard);
bindStressRangeControls('[data-korea-stress-ranges]', 'data-korea-stress-range', () => koreaStressRangeYears, (value) => { koreaStressRangeYears = value; }, loadKoreaStressDashboard);
bindStressRangeControls('[data-em-stress-ranges]', 'data-em-stress-range', () => emStressRangeYears, (value) => { emStressRangeYears = value; }, loadEmStressDashboard);
window.MacroWatchDashboard?.registerLoader(async () => {
  await Promise.all([
    loadNewsSentimentDashboard(),
    loadMarketStressDashboard(),
    loadCreditStressComponentsDashboard(),
    loadKoreaStressDashboard(),
    loadEmStressDashboard(),
    loadSectorFlowDashboard(),
  ]);
});
})();
