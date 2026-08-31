(() => {
  'use strict';

  const METRICS = [
    { key: 'revenue', label: '매출', className: 'revenue' },
    { key: 'operating_income', label: '영업이익', className: 'operating-income' },
    { key: 'net_income', label: '순이익', className: 'net-income' },
  ];
  const CHARTS = [
    { id: 'korea-earnings-amount-chart', valueKey: 'currentAverage', kind: 'amount', height: 320, includeZero: false, unit: '원' },
    { id: 'korea-earnings-growth-chart', valueKey: 'yoyPct', kind: 'growth', height: 220, includeZero: true, unit: '%' },
    { id: 'korea-earnings-delta-chart', valueKey: 'yoyDeltaPp', kind: 'delta', height: 220, includeZero: true, unit: '%p' },
  ];
  const AXIS_WIDTH = 64, MIN_WIDTH = 640;
  const PADDING = { top: 24, right: 24, bottom: 42, left: 14 };
  const state = { series: [], years: 5 };

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
  function formatAxis(value, kind) {
    if (kind === 'amount') return formatAmount(value);
    return `${Math.abs(value) < Number.EPSILON ? 0 : Number(value.toFixed(2))}`;
  }

  // 서버가 계산한 분기·항목별 행을 차트용 시계열 모양으로만 변환합니다.
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

  // 각 차트가 자기 단위와 현재 표시 구간에 맞는 Y축을 독립적으로 사용합니다.
  function axisDomain(values, { includeZero = false, paddingRatio = 0.1, targetIntervals = 4 } = {}) {
    const finiteValues = values.filter(Number.isFinite);
    if (!finiteValues.length) return { min: -1, max: 1, ticks: [-1, -0.5, 0, 0.5, 1] };
    let minimum = Math.min(...finiteValues), maximum = Math.max(...finiteValues);
    if (includeZero) { minimum = Math.min(0, minimum); maximum = Math.max(0, maximum); }
    const span = maximum - minimum;
    const padding = span > Number.EPSILON ? span * paddingRatio : Math.max(Math.abs(maximum) * paddingRatio, 1);
    const paddedMin = minimum - padding, paddedMax = maximum + padding;
    const step = window.MacroWatchAnalysisChart.niceStep((paddedMax - paddedMin) / targetIntervals);
    const domainMin = Math.floor(paddedMin / step) * step, domainMax = Math.ceil(paddedMax / step) * step;
    const tickCount = Math.round((domainMax - domainMin) / step);
    const ticks = Array.from({ length: tickCount + 1 }, (_, index) => Number((domainMin + (step * index)).toPrecision(12)));
    return { min: domainMin, max: domainMax, ticks };
  }

  function linePath(points, yMin, yMax, width, height) {
    const segments = [];
    let segment = [];
    points.forEach((point, index) => {
      if (!Number.isFinite(point.value)) {
        if (segment.length) segments.push(segment);
        segment = [];
        return;
      }
      segment.push({
        x: scale(index, 0, Math.max(points.length - 1, 1), PADDING.left, width - PADDING.right),
        y: scale(point.value, yMin, yMax, height - PADDING.bottom, PADDING.top),
      });
    });
    if (segment.length) segments.push(segment);
    return segments.map((segmentPoints) => window.MacroWatchAnalysisChart.monotonePath(segmentPoints)).join(' ');
  }

  function metricValue(point, metricKey, valueKey) { return point.metrics[metricKey]?.[valueKey] ?? null; }

  function visiblePoints() {
    const usable = state.series.filter((row) => METRICS.some((metric) => CHARTS.some((chart) => Number.isFinite(metricValue(row, metric.key, chart.valueKey)))));
    return state.years === 'max' ? usable : usable.slice(-Number(state.years) * 4);
  }

  function updateSummary(points) {
    const element = document.getElementById('korea-earnings-summary'), latest = points.at(-1);
    if (!element || !latest) return;
    const referenceMetric = latest.metrics.revenue || latest.metrics.operating_income || latest.metrics.net_income;
    const basis = referenceMetric?.universeBasis === 'point_in_time_market_cap_snapshot'
      ? '해당 분기 시총 순위 기준'
      : '과거 순위 미확보 · 대체 유니버스 평균';
    const values = METRICS.map((metric) => `<span>${metric.label} 평균 ${formatAmount(metricValue(latest, metric.key, 'currentAverage'))}원</span>`).join('');
    element.innerHTML = `<strong>${periodLabel(latest)}</strong>${values}<span>실적 반영 ${referenceMetric?.coverage || 0}/${latest.universeCount}사</span><span>${basis}</span>`;
  }

  function renderChart(spec, points) {
    const container = document.getElementById(spec.id);
    if (!container) return null;
    const values = points.flatMap((point) => METRICS.map((metric) => metricValue(point, metric.key, spec.valueKey))).filter(Number.isFinite);
    if (!values.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-40 items-center justify-center border border-dashed p-5 text-sm text-slate-500">표시할 비교 자료가 없습니다.</div>';
      return null;
    }
    const domain = axisDomain(values, { includeZero: spec.includeZero });
    const frameWidth = Math.max(MIN_WIDTH, (container.clientWidth || MIN_WIDTH) - AXIS_WIDTH);
    const chartWidth = Math.max(frameWidth, points.length * 48);
    const x = (index) => scale(index, 0, Math.max(points.length - 1, 1), PADDING.left, chartWidth - PADDING.right);
    const y = (value) => scale(value, domain.min, domain.max, spec.height - PADDING.bottom, PADDING.top);
    const axis = domain.ticks.map((value) => `<text x="58" y="${y(value) + 3}" text-anchor="end" class="korea-earnings-axis-label">${formatAxis(value, spec.kind)}</text>`).join('');
    const grids = domain.ticks.map((value) => `<line x1="${PADDING.left}" y1="${y(value)}" x2="${chartWidth - PADDING.right}" y2="${y(value)}" class="korea-earnings-grid${Math.abs(value) < Number.EPSILON ? ' korea-earnings-grid--zero' : ''}"/>`).join('');
    const labels = points.map((point, index) => point.fiscalQuarter === 1 || index === points.length - 1
      ? `<text x="${x(index)}" y="${spec.height - 12}" text-anchor="middle" class="korea-earnings-period-label">${point.fiscalQuarter === 1 ? point.fiscalYear : `Q${point.fiscalQuarter}`}</text>` : '').join('');
    const metricSeries = METRICS.map((metric) => ({
      ...metric,
      points: points.map((point) => ({ ...point, value: metricValue(point, metric.key, spec.valueKey) })),
    }));
    const lines = metricSeries.map((metric) => `<path d="${linePath(metric.points, domain.min, domain.max, chartWidth, spec.height)}" class="korea-earnings-line korea-earnings-line--${spec.kind} korea-earnings-line--${metric.className}"/>`).join('');
    const dots = metricSeries.flatMap((metric) => metric.points.map((point, index) => Number.isFinite(point.value)
      ? `<circle cx="${x(index)}" cy="${y(point.value)}" r="${spec.kind === 'amount' ? 2.8 : 2.4}" class="korea-earnings-point korea-earnings-point--${metric.className}"/>` : '')).join('');
    container.innerHTML = `<div class="korea-earnings-chart-layout"><svg class="korea-earnings-y-axis" style="height:${spec.height}px" viewBox="0 0 ${AXIS_WIDTH} ${spec.height}" aria-hidden="true">${axis}</svg><div class="korea-earnings-chart-frame"><svg class="korea-earnings-chart-svg" width="${chartWidth}" height="${spec.height}" viewBox="0 0 ${chartWidth} ${spec.height}" role="img" aria-label="매출·영업이익·순이익 ${spec.kind} 시계열">${grids}${labels}${lines}${dots}<line data-korea-earnings-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${spec.height - PADDING.bottom}" class="korea-earnings-cursor"/><text data-korea-earnings-cursor-label x="0" y="15" text-anchor="middle" class="korea-earnings-cursor-label"></text><rect x="0" y="0" width="${chartWidth}" height="${spec.height}" fill="transparent" data-korea-earnings-hit/></svg></div></div>`;
    const frame = container.querySelector('.korea-earnings-chart-frame'), hit = container.querySelector('[data-korea-earnings-hit]');
    const cursor = container.querySelector('[data-korea-earnings-cursor]'), cursorLabel = container.querySelector('[data-korea-earnings-cursor-label]');
    hit.addEventListener('pointermove', (event) => {
      const rect = hit.getBoundingClientRect(), localX = (event.clientX - rect.left) * (chartWidth / rect.width);
      const index = Math.max(0, Math.min(points.length - 1, Math.round(scale(localX, PADDING.left, chartWidth - PADDING.right, 0, Math.max(points.length - 1, 1)))));
      const point = points[index], cursorX = x(index);
      const details = METRICS.map((metric) => {
        const value = metricValue(point, metric.key, spec.valueKey);
        return `${metric.label} ${spec.kind === 'amount' ? `${formatAmount(value)}원` : formatSigned(value, spec.unit)}`;
      }).join(' · ');
      cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX); cursorLabel.setAttribute('x', cursorX);
      cursorLabel.textContent = `${periodLabel(point)} · ${details}`;
      cursor.classList.add('is-visible'); cursorLabel.classList.add('is-visible');
    });
    hit.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); cursorLabel.classList.remove('is-visible'); });
    window.MacroWatchAnalysisChart.scrollToLatest(frame);
    return frame;
  }

  function synchronizeFrames(frames) {
    let synchronizing = false;
    frames.forEach((source) => source.addEventListener('scroll', () => {
      if (synchronizing) return;
      synchronizing = true;
      const sourceRange = source.scrollWidth - source.clientWidth;
      const ratio = sourceRange > 0 ? source.scrollLeft / sourceRange : 0;
      frames.forEach((target) => {
        if (target !== source) target.scrollLeft = ratio * (target.scrollWidth - target.clientWidth);
      });
      synchronizing = false;
    }, { passive: true }));
  }

  function setStatus(message) {
    CHARTS.forEach((chart) => {
      const container = document.getElementById(chart.id);
      if (container) container.innerHTML = `<div class="analysis-empty-state-light flex min-h-40 items-center justify-center border border-dashed p-5 text-sm text-slate-500">${message}</div>`;
    });
  }

  function render() {
    const points = visiblePoints();
    if (!points.length) { setStatus('비교 가능한 KOSPI 시총 상위기업 평균 실적이 아직 없습니다.'); return; }
    updateSummary(points);
    const frames = CHARTS.map((chart) => renderChart(chart, points)).filter(Boolean);
    synchronizeFrames(frames);
  }

  async function load({ supabaseClient }) {
    if (!document.getElementById('korea-earnings-amount-chart') || !supabaseClient) return;
    const response = await window.MacroWatchAnalysisChart.loadAllRows((from, to) => supabaseClient
      .from('earnings_market_quarterly_metrics')
      .select('fiscal_year,fiscal_quarter,metric,universe_basis,universe_company_count,comparable_company_count,current_average,yoy_pct,yoy_state,yoy_delta_pp')
      .eq('index_id', 'KOSPI100')
      .order('fiscal_year').order('fiscal_quarter').order('metric').range(from, to));
    if (response.error) { setStatus('KOSPI 100 집계 실적을 불러오지 못했습니다.'); return; }
    state.series = seriesFromMetricRows(response.data || []);
    render();
  }

  document.querySelector('[data-korea-earnings-ranges]')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-korea-earnings-range]');
    if (!button) return;
    state.years = button.dataset.koreaEarningsRange === 'max' ? 'max' : Number(button.dataset.koreaEarningsRange);
    document.querySelectorAll('[data-korea-earnings-range]').forEach((item) => item.classList.toggle('is-active', item === button));
    render();
  });
  // 전용 기업 이익 메뉴가 표시된 뒤 숨김 상태에서 계산한 세 차트 폭을 다시 맞춥니다.
  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => { if (detail?.view === 'earnings') render(); });
  window.MacroWatchKoreaEarnings = Object.freeze({ seriesFromMetricRows, axisDomain });
  window.MacroWatchDashboard?.registerLoader(load);
})();
