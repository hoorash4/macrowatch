(() => {
  'use strict';

  const LABELS = { revenue: '매출', operating_income: '영업이익', net_income: '순이익' };
  const HEIGHT = 320, AMOUNT_AXIS_WIDTH = 64, RATE_AXIS_WIDTH = 52, MIN_WIDTH = 640;
  const PADDING = { top: 24, right: 24, bottom: 42, left: 14 };
  const state = { series: [], metric: 'revenue', years: 5 };

  function finite(value) { const number = Number(value); return Number.isFinite(number) ? number : null; }
  function periodLabel(row) { return `${row.fiscalYear} Q${row.fiscalQuarter}`; }
  function formatSigned(value, unit) { return Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}${unit}` : '—'; }
  function formatAmount(value) {
    if (!Number.isFinite(value)) return '—';
    const absolute = Math.abs(value);
    if (absolute >= 1e12) return `${(value / 1e12).toFixed(absolute >= 1e13 ? 0 : 1)}조`;
    if (absolute >= 1e8) return `${(value / 1e8).toFixed(absolute >= 1e10 ? 0 : 1)}억`;
    if (absolute >= 1e4) return `${(value / 1e4).toFixed(absolute >= 1e6 ? 0 : 1)}만`;
    return value.toLocaleString('ko-KR', { maximumFractionDigits: 0 });
  }

  // The browser only reshapes compact, precomputed server rows. Simple averages,
  // comparable cohorts, transition states and deltas are worker responsibilities.
  function seriesFromMetricRows(rows) {
    const periods = new Map();
    rows.forEach((row) => {
      const fiscalYear = Number(row.fiscal_year), fiscalQuarter = Number(row.fiscal_quarter);
      const key = `${fiscalYear}:${fiscalQuarter}`;
      const period = periods.get(key) || { fiscalYear, fiscalQuarter, metrics: {}, universeCount: Number(row.universe_company_count) || 0 };
      period.metrics[row.metric] = {
        yoyPct: finite(row.yoy_pct),
        yoyDeltaPp: finite(row.yoy_delta_pp),
        yoyState: row.yoy_state,
        coverage: Number(row.comparable_company_count) || 0,
        currentAverage: finite(row.current_average),
        universeBasis: row.universe_basis,
      };
      periods.set(key, period);
    });
    return [...periods.values()].sort((a, b) => (a.fiscalYear * 4 + a.fiscalQuarter) - (b.fiscalYear * 4 + b.fiscalQuarter));
  }

  function scale(value, sourceMin, sourceMax, targetMin, targetMax) {
    return sourceMax === sourceMin ? (targetMin + targetMax) / 2 : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);
  }

  function path(points, key, yMin, yMax, width) {
    const segments = [];
    let segment = [];
    points.forEach((point, index) => {
      if (!Number.isFinite(point[key])) {
        if (segment.length) segments.push(segment);
        segment = [];
        return;
      }
      segment.push({
        x: scale(index, 0, Math.max(points.length - 1, 1), PADDING.left, width - PADDING.right),
        y: scale(point[key], yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top),
      });
    });
    if (segment.length) segments.push(segment);
    return segments.map((pointsInSegment) => window.MacroWatchAnalysisChart.monotonePath(pointsInSegment)).join(' ');
  }

  function updateSummary(points) {
    const element = document.getElementById('korea-earnings-summary');
    const latest = points.at(-1);
    if (!element || !latest) return;
    const metric = latest.metrics[state.metric];
    const basis = metric.universeBasis === 'point_in_time_market_cap_snapshot'
      ? '해당 분기 시총 순위 기준'
      : '과거 순위 미확보 · 대체 유니버스 평균';
    element.innerHTML = `<strong>${LABELS[state.metric]} ${periodLabel(latest)}</strong><span>평균 ${formatAmount(metric.currentAverage)}원</span><span>증가율 ${formatSigned(metric.yoyPct, '%')}</span><span>델타 ${formatSigned(metric.yoyDeltaPp, '%p')}</span><span>실적 반영 ${metric.coverage}/${latest.universeCount}사</span><span>${basis}</span>`;
  }

  function render() {
    const container = document.getElementById('korea-earnings-chart');
    if (!container) return;
    const usable = state.series.filter((row) => {
      const metric = row.metrics[state.metric];
      return metric && [metric.currentAverage, metric.yoyPct, metric.yoyDeltaPp].some(Number.isFinite);
    });
    const points = state.years === 'max' ? usable : usable.slice(-Number(state.years) * 4);
    if (!points.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">비교 가능한 KOSPI 시총 상위기업 평균 실적이 아직 없습니다.</div>';
      return;
    }
    updateSummary(points);
    const rateValues = points.flatMap((row) => [row.metrics[state.metric].yoyPct, row.metrics[state.metric].yoyDeltaPp]).filter(Number.isFinite);
    const maximumRate = Math.max(1, ...rateValues.map(Math.abs));
    const rateTickStep = window.MacroWatchAnalysisChart.niceStep(maximumRate / 2);
    const rateMax = rateTickStep * 2, rateMin = -rateMax;
    const amountValues = points.map((row) => row.metrics[state.metric].currentAverage).filter(Number.isFinite);
    const rawAmountMin = Math.min(0, ...amountValues), rawAmountMax = Math.max(1, ...amountValues);
    const amountTickStep = window.MacroWatchAnalysisChart.niceStep(Math.max(1, rawAmountMax - rawAmountMin) / 4);
    const amountMin = Math.floor(rawAmountMin / amountTickStep) * amountTickStep;
    const amountMax = Math.max(amountTickStep, Math.ceil(rawAmountMax / amountTickStep) * amountTickStep);
    const frameWidth = Math.max(MIN_WIDTH, (container.clientWidth || MIN_WIDTH) - AMOUNT_AXIS_WIDTH - RATE_AXIS_WIDTH);
    const chartWidth = Math.max(frameWidth, points.length * 48);
    const yRate = (value) => scale(value, rateMin, rateMax, HEIGHT - PADDING.bottom, PADDING.top);
    const yAmount = (value) => scale(value, amountMin, amountMax, HEIGHT - PADDING.bottom, PADDING.top);
    const x = (index) => scale(index, 0, Math.max(points.length - 1, 1), PADDING.left, chartWidth - PADDING.right);
    const rateTicks = [-2, -1, 0, 1, 2];
    const amountTicks = Array.from({ length: 5 }, (_, index) => amountMin + ((amountMax - amountMin) * index / 4));
    const amountAxis = amountTicks.map((value) => `<text x="58" y="${yAmount(value) + 3}" text-anchor="end" class="korea-earnings-axis-label">${formatAmount(value)}</text>`).join('');
    const rateAxis = rateTicks.map((multiple) => `<text x="6" y="${yRate(multiple * rateTickStep) + 3}" text-anchor="start" class="korea-earnings-axis-label">${multiple * rateTickStep}%</text>`).join('');
    const grids = rateTicks.map((multiple) => `<line x1="${PADDING.left}" y1="${yRate(multiple * rateTickStep)}" x2="${chartWidth - PADDING.right}" y2="${yRate(multiple * rateTickStep)}" class="korea-earnings-grid${multiple === 0 ? ' korea-earnings-grid--zero' : ''}"/>`).join('');
    const periodLabels = points.map((point, index) => point.fiscalQuarter === 1 || index === points.length - 1 ? `<text x="${x(index)}" y="${HEIGHT - 12}" text-anchor="middle" class="korea-earnings-period-label">${point.fiscalQuarter === 1 ? point.fiscalYear : `Q${point.fiscalQuarter}`}</text>` : '').join('');
    const metricPoints = points.map((point) => ({ ...point, currentAverage: point.metrics[state.metric].currentAverage, yoyPct: point.metrics[state.metric].yoyPct, yoyDeltaPp: point.metrics[state.metric].yoyDeltaPp }));
    const dots = metricPoints.map((point, index) => [
      Number.isFinite(point.currentAverage) ? `<circle cx="${x(index)}" cy="${yAmount(point.currentAverage)}" r="2.8" class="korea-earnings-point korea-earnings-point--average"/>` : '',
      Number.isFinite(point.yoyPct) ? `<circle cx="${x(index)}" cy="${yRate(point.yoyPct)}" r="2.8" class="korea-earnings-point korea-earnings-point--growth"/>` : '',
      Number.isFinite(point.yoyDeltaPp) ? `<circle cx="${x(index)}" cy="${yRate(point.yoyDeltaPp)}" r="2.5" class="korea-earnings-point korea-earnings-point--delta"/>` : '',
    ].join('')).join('');
    container.innerHTML = `<div class="korea-earnings-chart-layout"><svg class="korea-earnings-y-axis korea-earnings-y-axis--amount" viewBox="0 0 ${AMOUNT_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${amountAxis}</svg><div class="korea-earnings-chart-frame"><svg class="korea-earnings-chart-svg" width="${chartWidth}" height="${HEIGHT}" viewBox="0 0 ${chartWidth} ${HEIGHT}" role="img" aria-label="${LABELS[state.metric]} 평균 금액, 전년동기 증가율과 증가율 델타">${grids}${periodLabels}<path d="${path(metricPoints, 'currentAverage', amountMin, amountMax, chartWidth)}" class="korea-earnings-line korea-earnings-line--average"/><path d="${path(metricPoints, 'yoyPct', rateMin, rateMax, chartWidth)}" class="korea-earnings-line korea-earnings-line--growth"/><path d="${path(metricPoints, 'yoyDeltaPp', rateMin, rateMax, chartWidth)}" class="korea-earnings-line korea-earnings-line--delta"/>${dots}<line data-korea-earnings-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="korea-earnings-cursor"/><text data-korea-earnings-cursor-label x="0" y="15" text-anchor="middle" class="korea-earnings-cursor-label"></text><rect x="0" y="0" width="${chartWidth}" height="${HEIGHT}" fill="transparent" data-korea-earnings-hit/></svg></div><svg class="korea-earnings-y-axis korea-earnings-y-axis--rate" viewBox="0 0 ${RATE_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${rateAxis}</svg></div>`;
    const frame = container.querySelector('.korea-earnings-chart-frame'), hit = container.querySelector('[data-korea-earnings-hit]');
    const cursor = container.querySelector('[data-korea-earnings-cursor]'), cursorLabel = container.querySelector('[data-korea-earnings-cursor-label]');
    hit.addEventListener('pointermove', (event) => {
      const rect = hit.getBoundingClientRect(), localX = (event.clientX - rect.left) * (chartWidth / rect.width);
      const index = Math.max(0, Math.min(metricPoints.length - 1, Math.round(scale(localX, PADDING.left, chartWidth - PADDING.right, 0, Math.max(metricPoints.length - 1, 1)))));
      const point = metricPoints[index], cursorX = x(index), metric = point.metrics[state.metric];
      cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX); cursorLabel.setAttribute('x', cursorX);
      cursorLabel.textContent = `${periodLabel(point)} · 평균 ${formatAmount(metric.currentAverage)}원 · ${formatSigned(metric.yoyPct, '%')} · Δ ${formatSigned(metric.yoyDeltaPp, '%p')} · ${metric.coverage}/${point.universeCount}사`;
      cursor.classList.add('is-visible'); cursorLabel.classList.add('is-visible');
    });
    hit.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); cursorLabel.classList.remove('is-visible'); });
    window.MacroWatchAnalysisChart.scrollToLatest(frame);
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('korea-earnings-chart');
    if (!container || !supabaseClient) return;
    const response = await window.MacroWatchAnalysisChart.loadAllRows((from, to) => supabaseClient
      .from('earnings_market_quarterly_metrics')
      .select('fiscal_year,fiscal_quarter,metric,universe_basis,universe_company_count,comparable_company_count,current_average,yoy_pct,yoy_state,yoy_delta_pp')
      .eq('index_id', 'KOSPI100')
      .order('fiscal_year').order('fiscal_quarter').order('metric').range(from, to));
    if (response.error) { container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">KOSPI 100 집계 실적을 불러오지 못했습니다.</div>'; return; }
    state.series = seriesFromMetricRows(response.data || []);
    render();
  }

  document.querySelector('.korea-earnings-tabs')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-korea-earnings-metric]');
    if (!button) return;
    state.metric = button.dataset.koreaEarningsMetric;
    document.querySelectorAll('[data-korea-earnings-metric]').forEach((item) => { const active = item === button; item.classList.toggle('is-active', active); item.setAttribute('aria-selected', String(active)); });
    render();
  });
  document.querySelector('[data-korea-earnings-ranges]')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-korea-earnings-range]');
    if (!button) return;
    state.years = button.dataset.koreaEarningsRange === 'max' ? 'max' : Number(button.dataset.koreaEarningsRange);
    document.querySelectorAll('[data-korea-earnings-range]').forEach((item) => item.classList.toggle('is-active', item === button));
    render();
  });
  // 전용 기업 이익 메뉴가 표시된 뒤 숨김 상태에서 계산한 차트 폭을 다시 맞춥니다.
  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => { if (detail?.view === 'earnings') render(); });
  window.MacroWatchKoreaEarnings = Object.freeze({ seriesFromMetricRows });
  window.MacroWatchDashboard?.registerLoader(load);
})();
