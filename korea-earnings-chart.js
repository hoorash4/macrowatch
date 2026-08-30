(() => {
  'use strict';

  const LABELS = { revenue: '매출', operating_income: '영업이익', net_income: '순이익' };
  const HEIGHT = 320, Y_AXIS_WIDTH = 52, MIN_WIDTH = 640;
  const PADDING = { top: 24, right: 24, bottom: 42, left: 14 };
  const state = { series: [], metric: 'revenue', years: 5 };

  function finite(value) { const number = Number(value); return Number.isFinite(number) ? number : null; }
  function periodLabel(row) { return `${row.fiscalYear} Q${row.fiscalQuarter}`; }
  function formatSigned(value, unit) { return Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}${unit}` : '—'; }

  // The browser only reshapes compact, precomputed server rows. Signed sums,
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
        currentTotal: finite(row.current_total),
      };
      periods.set(key, period);
    });
    return [...periods.values()].sort((a, b) => (a.fiscalYear * 4 + a.fiscalQuarter) - (b.fiscalYear * 4 + b.fiscalQuarter));
  }

  function scale(value, sourceMin, sourceMax, targetMin, targetMax) {
    return sourceMax === sourceMin ? (targetMin + targetMax) / 2 : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);
  }

  function path(points, key, yMin, yMax, width) {
    return points.map((point, index) => {
      const x = scale(index, 0, Math.max(points.length - 1, 1), PADDING.left, width - PADDING.right);
      const y = scale(point[key], yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top);
      return `${index ? 'L' : 'M'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(' ');
  }

  function updateSummary(points) {
    const element = document.getElementById('korea-earnings-summary');
    const latest = points.at(-1);
    if (!element || !latest) return;
    const metric = latest.metrics[state.metric];
    element.innerHTML = `<strong>${LABELS[state.metric]} ${periodLabel(latest)}</strong><span>증가율 ${formatSigned(metric.yoyPct, '%')}</span><span>델타 ${formatSigned(metric.yoyDeltaPp, '%p')}</span><span>동일기업 ${metric.coverage}/${latest.universeCount}사</span><span>현재 구성 종목 기준</span>`;
  }

  function render() {
    const container = document.getElementById('korea-earnings-chart');
    if (!container) return;
    const usable = state.series.filter((row) => Number.isFinite(row.metrics[state.metric]?.yoyPct) && Number.isFinite(row.metrics[state.metric]?.yoyDeltaPp));
    const points = state.years === 'max' ? usable : usable.slice(-Number(state.years) * 4);
    if (!points.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">비교 가능한 KOSPI 100 합산 실적이 아직 없습니다.</div>';
      return;
    }
    updateSummary(points);
    const values = points.flatMap((row) => [row.metrics[state.metric].yoyPct, row.metrics[state.metric].yoyDeltaPp]).filter(Number.isFinite);
    const maximum = Math.max(1, ...values.map(Math.abs));
    const tickStep = window.MacroWatchAnalysisChart.niceStep(maximum / 2);
    const yMax = tickStep * 2, yMin = -yMax;
    const frameWidth = Math.max(MIN_WIDTH, (container.clientWidth || MIN_WIDTH) - Y_AXIS_WIDTH);
    const chartWidth = Math.max(frameWidth, points.length * 48);
    const y = (value) => scale(value, yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top);
    const x = (index) => scale(index, 0, Math.max(points.length - 1, 1), PADDING.left, chartWidth - PADDING.right);
    const ticks = [-2, -1, 0, 1, 2];
    const yAxis = ticks.map((multiple) => `<text x="45" y="${y(multiple * tickStep) + 3}" text-anchor="end" class="korea-earnings-axis-label">${multiple * tickStep}%</text>`).join('');
    const grids = ticks.map((multiple) => `<line x1="${PADDING.left}" y1="${y(multiple * tickStep)}" x2="${chartWidth - PADDING.right}" y2="${y(multiple * tickStep)}" class="korea-earnings-grid${multiple === 0 ? ' korea-earnings-grid--zero' : ''}"/>`).join('');
    const periodLabels = points.map((point, index) => point.fiscalQuarter === 1 || index === points.length - 1 ? `<text x="${x(index)}" y="${HEIGHT - 12}" text-anchor="middle" class="korea-earnings-period-label">${point.fiscalQuarter === 1 ? point.fiscalYear : `Q${point.fiscalQuarter}`}</text>` : '').join('');
    const metricPoints = points.map((point) => ({ ...point, yoyPct: point.metrics[state.metric].yoyPct, yoyDeltaPp: point.metrics[state.metric].yoyDeltaPp }));
    const dots = metricPoints.map((point, index) => `<circle cx="${x(index)}" cy="${y(point.yoyPct)}" r="2.8" class="korea-earnings-point korea-earnings-point--growth"/><circle cx="${x(index)}" cy="${y(point.yoyDeltaPp)}" r="2.5" class="korea-earnings-point korea-earnings-point--delta"/>`).join('');
    container.innerHTML = `<div class="korea-earnings-chart-layout"><svg class="korea-earnings-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${yAxis}</svg><div class="korea-earnings-chart-frame"><svg class="korea-earnings-chart-svg" width="${chartWidth}" height="${HEIGHT}" viewBox="0 0 ${chartWidth} ${HEIGHT}" role="img" aria-label="${LABELS[state.metric]} 합산 전년동기 증가율과 증가율 델타">${grids}${periodLabels}<path d="${path(metricPoints, 'yoyPct', yMin, yMax, chartWidth)}" class="korea-earnings-line korea-earnings-line--growth"/><path d="${path(metricPoints, 'yoyDeltaPp', yMin, yMax, chartWidth)}" class="korea-earnings-line korea-earnings-line--delta"/>${dots}<line data-korea-earnings-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="korea-earnings-cursor"/><text data-korea-earnings-cursor-label x="0" y="15" text-anchor="middle" class="korea-earnings-cursor-label"></text><rect x="0" y="0" width="${chartWidth}" height="${HEIGHT}" fill="transparent" data-korea-earnings-hit/></svg></div></div>`;
    const frame = container.querySelector('.korea-earnings-chart-frame'), hit = container.querySelector('[data-korea-earnings-hit]');
    const cursor = container.querySelector('[data-korea-earnings-cursor]'), cursorLabel = container.querySelector('[data-korea-earnings-cursor-label]');
    hit.addEventListener('pointermove', (event) => {
      const rect = hit.getBoundingClientRect(), localX = (event.clientX - rect.left) * (chartWidth / rect.width);
      const index = Math.max(0, Math.min(metricPoints.length - 1, Math.round(scale(localX, PADDING.left, chartWidth - PADDING.right, 0, Math.max(metricPoints.length - 1, 1)))));
      const point = metricPoints[index], cursorX = x(index), metric = point.metrics[state.metric];
      cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX); cursorLabel.setAttribute('x', cursorX);
      cursorLabel.textContent = `${periodLabel(point)} · ${formatSigned(metric.yoyPct, '%')} · Δ ${formatSigned(metric.yoyDeltaPp, '%p')} · ${metric.coverage}/${point.universeCount}사`;
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
      .select('fiscal_year,fiscal_quarter,metric,universe_company_count,comparable_company_count,current_total,yoy_pct,yoy_state,yoy_delta_pp')
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
  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => { if (detail?.view === 'korea') render(); });
  window.MacroWatchKoreaEarnings = Object.freeze({ seriesFromMetricRows });
  window.MacroWatchDashboard?.registerLoader(load);
})();
