(() => {
'use strict';

// 뉴스 흐름과 시장 스트레스 시각화 전용 모듈입니다.
// 서버에서 저장된 데이터를 읽고 차트 DOM을 만드는 책임만 가지며,
// 추적 항목 CRUD와 드래그 상태에는 접근하지 않습니다.
const { escapeHtml } = window.MacroWatchFrontend;
const supabaseClient = window.macroWatchSupabase
  || window.MacroWatchFrontend.createSupabaseClient();

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

// ===== 뉴스 흐름 분석 모듈 =====
// 일별 집계 데이터 조회, 긍정·부정 비율 계산, 기간별 막대 렌더링을 담당한다.
// 기사 분류와 저장은 서버에서 수행하므로 이 구역은 읽기와 화면 표시만 맡는다.
const DECISIVE_NEWS_KEYWORD_EXAMPLES = ['금융기관 부실', '신용시장 경색', '감염병 확산'];

function normalizeDecisiveNewsKeywords(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((keyword) => String(keyword).trim()).filter(Boolean))].slice(0, 8);
}

function renderDecisiveNewsKeywords(container, values) {
  if (!container) return;
  const isExample = values.length === 0;
  const keywords = isExample ? DECISIVE_NEWS_KEYWORD_EXAMPLES : values;
  const label = isExample ? '<span class="decisive-news-keyword-label">표시 예시</span>' : '';
  container.innerHTML = `${label}${keywords.map((keyword) => `<span class="decisive-news-keyword${isExample ? ' is-example' : ''}">${escapeHtml(keyword)}</span>`).join('')}`;
}

function renderExtremeNewsSignals(rows) {
  const decisive = document.getElementById('decisive-news-count');
  const keywords = document.getElementById('decisive-news-keywords');
  if (!decisive) return;
  const latest = [...rows].sort((a, b) => String(a.article_date).localeCompare(String(b.article_date))).at(-1);
  if (!latest) {
    renderDecisiveNewsKeywords(keywords, []);
    return;
  }
  decisive.textContent = `${Number(latest.decisive_news_count || 0)}건`;
  renderDecisiveNewsKeywords(keywords, normalizeDecisiveNewsKeywords(latest.decisive_news_keywords));
  document.querySelectorAll('#news-extreme-signals [data-extreme-signal-status]').forEach((element) => { element.textContent = '자정 기준 집계'; });
}

function formatNewsDate(value) {
  const date = new Date(`${String(value || '')}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return '—';
  date.setUTCDate(date.getUTCDate() - 1);
  return `${date.getUTCMonth() + 1}/${date.getUTCDate()}`;
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
    chart.innerHTML = '<div class="analysis-empty-state-light col-span-full flex min-h-40 items-center justify-center rounded-xl border border-dashed border-slate-800 bg-slate-950/30 p-5 text-sm text-slate-500">다음 뉴스 분석 후 최근 3일 추이가 표시됩니다.</div>';
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
    ? 'flex h-60 min-w-0 flex-col justify-center gap-5 rounded-xl border border-slate-200 bg-slate-50 px-4 py-6'
    // 데이터가 기간을 채우기 전에는 왼쪽부터 쌓고, 가득 차면 자연스럽게 스크롤한다.
    : `flex h-60 min-w-0 items-end justify-start ${view.gapClass} overflow-x-auto rounded-xl border border-slate-200 bg-slate-50 px-4 py-4`;
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
  const width = 920, height = CREDIT_STRESS_CHART_HEIGHT, padding = { top: 20, right: 58, bottom: 32, left: 52 };
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

// ===== 이머징 시장 스트레스 모듈 =====
// 이머징 지수와 비교 자산의 공통 주간 시계열을 렌더링한다.
function renderEmStressDashboard(rows) {
  const chart = document.getElementById('em-stress-chart');
  const weekly = [...rows]
    .filter((row) => Number.isFinite(Number(row.stress_index)))
    .sort((a, b) => String(a.week).localeCompare(String(b.week)));
  if (!chart || !weekly.length) return;
  const width = 920, height = CREDIT_STRESS_CHART_HEIGHT, padding = { top: 20, right: 52, bottom: 32, left: 52 };
  const dates = weekly.map((row) => new Date(row.week).getTime());
  const start = Math.min(...dates), end = Math.max(...dates);
  const x = (value) => padding.left + ((new Date(value).getTime() - start) / Math.max(1, end - start)) * (width - padding.left - padding.right);
  const values = weekly.map((row) => Number(row.stress_index));
  const minimum = Math.min(...values), maximum = Math.max(...values), range = Math.max(maximum - minimum, 1);
  const lower = Math.max(0, minimum - range * .1), upper = maximum + range * .1;
  const y = (value) => padding.top + ((height - padding.top - padding.bottom) * (upper - value)) / Math.max(1, upper - lower);
  const eemValues = weekly.map((row) => Number(row.eem_weekly_close)).filter(Number.isFinite);
  const hasEem = eemValues.length > 1;
  const eemMinimum = hasEem ? Math.min(...eemValues) : 0;
  const eemMaximum = hasEem ? Math.max(...eemValues) : 1;
  const eemRange = Math.max(eemMaximum - eemMinimum, 1);
  const eemLower = Math.max(0, eemMinimum - eemRange * .1);
  const eemUpper = eemMaximum + eemRange * .1;
  const eemY = (value) => padding.top + ((height - padding.top - padding.bottom) * (eemUpper - value)) / Math.max(1, eemUpper - eemLower);
  const yearRows = weekly.filter((row, index) => index === 0 || String(row.week).slice(0, 4) !== String(weekly[index - 1].week).slice(0, 4));
  const yearGuides = yearRows.slice(1).map((row) => `<line x1="${x(row.week)}" x2="${x(row.week)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>`).join('');
  const years = yearRows.slice(1).map((row) => `<text x="${x(row.week)}" y="${height - 10}" text-anchor="middle" fill="#64748b" font-size="10">${String(row.week).slice(0, 4)}</text>`).join('');
  const grid = Array.from({ length: 5 }, (_, index) => {
    const value = upper - (upper - lower) * index / 4;
    return `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(value)}" y2="${y(value)}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text x="${padding.left - 9}" y="${y(value) + 3}" text-anchor="end" fill="#64748b" font-size="10">${value.toFixed(1)}</text>`;
  }).join('');
  const eem = hasEem ? weekly.slice(1).map((row, index) => {
    const previous = weekly[index];
    const before = Number(previous.eem_weekly_close), current = Number(row.eem_weekly_close);
    return Number.isFinite(before) && Number.isFinite(current) ? `<line x1="${x(previous.week)}" y1="${eemY(before)}" x2="${x(row.week)}" y2="${eemY(current)}" stroke="#6b7280" stroke-width="2" stroke-linecap="round"/>` : '';
  }).join('') : '';
  const eemLabels = hasEem ? [eemLower, (eemLower + eemUpper) / 2, eemUpper].map((value) => `<text x="${width - padding.right + 8}" y="${eemY(value) + 3}" fill="#6b7280" font-size="10">${value.toFixed(1)}</text>`).join('') : '';
  const paths = [];
  let path = '', provisionalPath = null;
  const finishPath = () => {
    if (!path) return;
    paths.push(`<path d="${path}" fill="none" stroke="${provisionalPath ? '#d97706' : '#00838c'}" stroke-width="3.25" stroke-linecap="round"${provisionalPath ? ' stroke-dasharray="4 3"' : ''}/>`);
    path = '';
  };
  weekly.slice(1).forEach((row, index) => {
    const previous = weekly[index];
    const provisional = Boolean(previous.is_provisional || row.is_provisional);
    const endpoint = `${x(row.week)} ${y(Number(row.stress_index))}`;
    if (provisionalPath !== provisional) {
      finishPath();
      provisionalPath = provisional;
      path = `M ${x(previous.week)} ${y(Number(previous.stress_index))} L ${endpoint}`;
    } else {
      path += ` L ${endpoint}`;
    }
  });
  finishPath();
  chart.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="이머징 시장 스트레스 지수와 EEM 주간 종가 추이"><line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/><line x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#94a3b8"/>${grid}${yearGuides}${eem}${paths.join('')}${eemLabels}${years}</svg>`;

  const createElement = (name, attributes) => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  };
  const attachVerticalGuide = ({ host, source, showLabels }) => {
    const svg = host?.querySelector('svg');
    if (!svg) return;
    const guide = createElement('line', { y1: padding.top, y2: height - padding.bottom, stroke: '#94a3b8', 'stroke-width': .75, 'stroke-dasharray': '3 4', 'pointer-events': 'none', visibility: 'hidden' });
    const valueLabel = showLabels ? createElement('text', { 'text-anchor': 'middle', fill: '#334155', 'font-size': 11, 'font-weight': 700, stroke: '#f8fafc', 'stroke-width': 4, 'paint-order': 'stroke', 'pointer-events': 'none', visibility: 'hidden' }) : null;
    const periodLabel = showLabels ? createElement('text', { 'text-anchor': 'middle', fill: '#64748b', 'font-size': 10, 'pointer-events': 'none', visibility: 'hidden' }) : null;
    svg.append(guide);
    if (valueLabel && periodLabel) svg.append(valueLabel, periodLabel);
    const show = (week) => {
      const nearest = weekly.reduce((closest, row) => Math.abs(x(row.week) - x(week)) < Math.abs(x(closest.week) - x(week)) ? row : closest);
      const pointX = x(nearest.week);
      guide.setAttribute('x1', pointX); guide.setAttribute('x2', pointX); guide.setAttribute('visibility', 'visible');
      if (valueLabel && periodLabel) {
        const [, month, day] = String(nearest.week).split('-').map(Number);
        valueLabel.setAttribute('x', pointX); valueLabel.setAttribute('y', padding.top + 11); valueLabel.setAttribute('visibility', 'visible');
        valueLabel.textContent = `EM-MSI ${Number(nearest.stress_index).toFixed(2)}${Number.isFinite(Number(nearest.eem_weekly_close)) ? ` · EEM ${Number(nearest.eem_weekly_close).toFixed(2)}` : ''}`;
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
      .order('week', { ascending: false }).limit(160);
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
  const fsiYearGuides = fsiRows
    .filter((row, index) => index > 0 && String(row.month).slice(0, 4) !== String(fsiRows[index - 1].month).slice(0, 4))
    .map((row) => `<line x1="${x(row.month)}" x2="${x(row.month)}" y1="${fsiPadding.top}" y2="${fsiHeight - fsiPadding.bottom}" stroke="#d4dde8" stroke-dasharray="3 4"/>`)
    .join('');
  const fsiLine = fsiRows.slice(1).map((row, index) => `<line x1="${x(fsiRows[index].month)}" y1="${fsiY(Number(fsiRows[index].bok_fsi))}" x2="${x(row.month)}" y2="${fsiY(Number(row.bok_fsi))}" stroke="#6d4b91" stroke-width="2.25" stroke-linecap="round"/>`).join('');
  fsiChart.innerHTML = `<svg class="w-full" style="height:${fsiHeight}px" viewBox="0 0 ${width} ${fsiHeight}" role="img" aria-label="한국은행 금융불안지수 보조지표"><line x1="${fsiPadding.left}" x2="${fsiPadding.left}" y1="${fsiPadding.top}" y2="${fsiHeight - fsiPadding.bottom}" stroke="#b6a8d0"/><line x1="${width - fsiPadding.right}" x2="${width - fsiPadding.right}" y1="${fsiPadding.top}" y2="${fsiHeight - fsiPadding.bottom}" stroke="#b6a8d0"/>${fsiGrid}${fsiYearGuides}${fsiLine}</svg>`;
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
    chart.innerHTML = '<div class="analysis-empty-state-light flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">신용위험 데이터를 불러오지 못했습니다.</div>';
  }
}

// 기존 대시보드 공개 계산 계약과 초기 로더 등록을 유지합니다.
window.MacroWatchChartUtils = Object.freeze({ calculateCorrelation, formatNewsDate });
window.MacroWatchDashboard?.registerLoader(async () => {
  await Promise.all([
    loadNewsSentimentDashboard(),
    loadMarketStressDashboard(),
    loadCreditStressComponentsDashboard(),
    loadKoreaStressDashboard(),
    loadEmStressDashboard(),
  ]);
});
})();
