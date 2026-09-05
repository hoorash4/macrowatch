(() => {
  'use strict';

  const METRICS = [
    { key: 'operating_income', label: '영업이익', className: 'operating-income' },
    { key: 'net_income', label: '순이익', className: 'net-income' },
  ];
  const CHARTS = [
    { id: 'korea-earnings-amount-chart', valueKey: 'amount', kind: 'amount', height: 320, includeZero: false, unit: '원', showPeriodLabels: true },
    { id: 'korea-earnings-margin-chart', valueKey: 'marginPct', kind: 'margin', height: 140, includeZero: true, unit: '%', showPeriodLabels: false },
    { id: 'korea-earnings-growth-chart', valueKey: 'yoyPct', kind: 'growth', height: 140, includeZero: true, unit: '%', showPeriodLabels: false },
    { id: 'korea-earnings-qoq-chart', valueKey: 'qoqPct', kind: 'qoq', height: 140, includeZero: true, unit: '%', showPeriodLabels: false },
  ];
  const AXIS_WIDTH = 64, MIN_WIDTH = 640;
  const DISPLAY_START_YEAR = 2016;
  const BASE_PADDING = { top: 24, right: 24, left: 14 };
  const state = { series: [], years: 5 };

  // Number(null)은 0이므로 DB의 계산 불가값을 먼저 걸러야 가짜 0점이 생기지 않습니다.
  function finite(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  function periodLabel(row) { return `${row.fiscalYear} Q${row.fiscalQuarter}`; }
  function formatSigned(value, unit) {
    if (!Number.isFinite(value)) return '—';
    const rounded = Math.abs(value) < .05 ? 0 : value;
    return `${rounded > 0 ? '+' : ''}${rounded.toFixed(1)}${unit}`;
  }
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

  // V2 공개 RPC의 분기 총합 행을 차트 전용 구조로만 변환합니다.
  function seriesFromMarketRows(rows) {
    return rows.filter((row) => Number(row.market_year) >= DISPLAY_START_YEAR).map((row) => ({
      fiscalYear: Number(row.market_year), fiscalQuarter: Number(row.market_quarter),
      reportedCount: Number(row.reported_company_count) || 0,
      pendingCount: Number(row.pending_company_count) || 0,
      universeCount: Number(row.target_company_count) || 0,
      lifecycleStatus: row.lifecycle_status,
      metrics: {
        operating_income: {
          amount: finite(row.operating_income_sa_total), rawAmount: finite(row.operating_income_total),
          marginPct: finite(row.operating_margin_pct), yoyPct: finite(row.operating_income_yoy_pct),
          yoyState: row.operating_income_yoy_state, qoqPct: finite(row.operating_income_qoq_sa_pct),
          qoqState: row.operating_income_qoq_state,
        },
        net_income: {
          amount: finite(row.net_income_sa_total), rawAmount: finite(row.net_income_total),
          marginPct: finite(row.net_margin_pct), yoyPct: finite(row.net_income_yoy_pct),
          yoyState: row.net_income_yoy_state, qoqPct: finite(row.net_income_qoq_sa_pct),
          qoqState: row.net_income_qoq_state,
        },
      },
    })).sort((a, b) => (a.fiscalYear * 4 + a.fiscalQuarter) - (b.fiscalYear * 4 + b.fiscalQuarter));
  }

  function scale(value, sourceMin, sourceMax, targetMin, targetMax) {
    return sourceMax === sourceMin ? (targetMin + targetMax) / 2 : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);
  }

  // 각 차트가 자기 단위와 현재 표시 구간에 맞는 Y축을 독립적으로 사용합니다.
  function axisDomain(values, { includeZero = false, paddingRatio = 0.05, targetIntervals = 4 } = {}) {
    const finiteValues = values.filter(Number.isFinite);
    if (!finiteValues.length) return { min: -1, max: 1, ticks: [-1, -0.5, 0, 0.5, 1] };
    let minimum = Math.min(...finiteValues), maximum = Math.max(...finiteValues);
    if (includeZero) { minimum = Math.min(0, minimum); maximum = Math.max(0, maximum); }
    const span = maximum - minimum;
    const padding = span > Number.EPSILON ? span * paddingRatio : Math.max(Math.abs(maximum) * paddingRatio, 1);
    // 증가율이 한쪽 부호에만 있을 때 0 반대편까지 축을 넓히지 않습니다.
    const paddedMin = includeZero && minimum === 0 ? 0 : minimum - padding;
    const paddedMax = includeZero && maximum === 0 ? 0 : maximum + padding;
    // 눈금 반올림으로 표시 범위를 다시 넓히지 않습니다. 현재 보이는
    // 값이 차트 높이를 충분히 사용하도록 최소 여백만 둔 5개 눈금입니다.
    const domainMin = paddedMin, domainMax = paddedMax;
    const ticks = includeZero && domainMin < 0 && domainMax > 0
      ? [domainMin, domainMin / 2, 0, domainMax / 2, domainMax]
      : Array.from({ length: targetIntervals + 1 }, (_, index) => (
        Number((domainMin + ((domainMax - domainMin) * index / targetIntervals)).toPrecision(12))
      ));
    return { min: domainMin, max: domainMax, ticks };
  }

  function linePath(points, yMin, yMax, width, height, padding) {
    const segments = [];
    let segment = [];
    points.forEach((point, index) => {
      if (!Number.isFinite(point.value)) {
        if (segment.length) segments.push(segment);
        segment = [];
        return;
      }
      segment.push({
        x: scale(index, 0, Math.max(points.length - 1, 1), padding.left, width - padding.right),
        y: scale(point.value, yMin, yMax, height - padding.bottom, padding.top),
      });
    });
    if (segment.length) segments.push(segment);
    return segments.map((segmentPoints) => window.MacroWatchAnalysisChart.monotonePath(segmentPoints)).join(' ');
  }

  function metricValue(point, metricKey, valueKey) { return point.metrics[metricKey]?.[valueKey] ?? null; }

  const STATE_LABELS = Object.freeze({ black_turn: '흑전', red_turn: '적전', from_zero: '계산 불가' });
  function metricState(point, metricKey, kind) {
    if (kind === 'growth') return point.metrics[metricKey]?.yoyState || '';
    if (kind === 'qoq') return point.metrics[metricKey]?.qoqState || '';
    return '';
  }
  function chartValue(point, metricKey, spec) {
    const value = metricValue(point, metricKey, spec.valueKey);
    if (Number.isFinite(value)) return value;
    return spec.kind !== 'amount' && STATE_LABELS[metricState(point, metricKey, spec.kind)] ? 0 : null;
  }
  function formatChartValue(point, metric, spec) {
    const raw = metricValue(point, metric.key, spec.valueKey);
    if (Number.isFinite(raw)) {
      if (spec.kind !== 'amount') return formatSigned(raw, spec.unit);
      const actual = metricValue(point, metric.key, 'rawAmount');
      return `${formatAmount(raw)}원${Number.isFinite(actual) ? ` (원본 ${formatAmount(actual)}원)` : ''}`;
    }
    return STATE_LABELS[metricState(point, metric.key, spec.kind)] || '—';
  }

  function visiblePoints() {
    const usable = state.series.filter((row) => METRICS.some((metric) => CHARTS.some((chart) => Number.isFinite(metricValue(row, metric.key, chart.valueKey)))));
    return state.years === 'max' ? usable : usable.slice(-Number(state.years) * 4);
  }

  function updateSummary(points) {
    const element = document.getElementById('korea-earnings-summary'), latest = points.at(-1);
    if (!element || !latest) return;
    const status = latest.lifecycleStatus === 'complete' ? '확정' : latest.lifecycleStatus === 'provisional' ? '잠정' : '수집 중';
    const values = METRICS.map((metric) => `<span>${metric.label} 계절조정 합계 ${formatAmount(metricValue(latest, metric.key, 'amount'))}원</span>`).join('');
    element.innerHTML = `<strong>${periodLabel(latest)}</strong>${values}<span>실적 반영 ${latest.reportedCount}/${latest.universeCount}사</span><span>${status}${latest.pendingCount ? ` · 대기 ${latest.pendingCount}사` : ''}</span>`;
  }

  function renderChart(spec, points) {
    const container = document.getElementById(spec.id);
    if (!container) return null;
    // 모든 차트에서 영업이익과 순이익을 같은 축에 함께 표시해 두 지표의
    // 절대 수준과 변화 방향을 한눈에 비교합니다.
    const chartMetrics = METRICS;
    const values = points.flatMap((point) => chartMetrics.map((metric) => chartValue(point, metric.key, spec))).filter(Number.isFinite);
    if (!values.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-40 items-center justify-center border border-dashed p-5 text-sm text-slate-500">표시할 비교 자료가 없습니다.</div>';
      return null;
    }
    const domainFor = (sourcePoints) => axisDomain(sourcePoints
      .flatMap((point) => chartMetrics.map((metric) => chartValue(point, metric.key, spec)))
      .filter(Number.isFinite), { includeZero: spec.includeZero });
    const domain = domainFor(points);
    const padding = { ...BASE_PADDING, bottom: spec.showPeriodLabels ? 42 : 16 };
    const frameWidth = Math.max(MIN_WIDTH, (container.clientWidth || MIN_WIDTH) - AXIS_WIDTH);
    const chartWidth = Math.max(frameWidth, points.length * 48);
    const x = (index) => scale(index, 0, Math.max(points.length - 1, 1), padding.left, chartWidth - padding.right);
    const y = (value, sourceDomain = domain) => scale(value, sourceDomain.min, sourceDomain.max, spec.height - padding.bottom, padding.top);
    const axis = domain.ticks.map((value, index) => `<text data-korea-earnings-y-label="${index}" x="58" y="${y(value) + 3}" text-anchor="end" class="korea-earnings-axis-label">${formatAxis(value, spec.kind)}</text>`).join('');
    const grids = domain.ticks.map((value, index) => `<line data-korea-earnings-y-grid="${index}" x1="${padding.left}" y1="${y(value)}" x2="${chartWidth - padding.right}" y2="${y(value)}" class="korea-earnings-grid${Math.abs(value) < Number.EPSILON ? ' korea-earnings-grid--zero' : ''}"/>`).join('');
    const labels = spec.showPeriodLabels
      ? points.map((point, index) => point.fiscalQuarter === 1 || index === points.length - 1
        ? `<text x="${x(index)}" y="${spec.height - 12}" text-anchor="middle" class="korea-earnings-period-label">${point.fiscalQuarter === 1 ? point.fiscalYear : `Q${point.fiscalQuarter}`}</text>`
        : '').join('')
      : '';
    const metricSeries = chartMetrics.map((metric) => ({
      ...metric,
      points: points.map((point) => ({ ...point, value: chartValue(point, metric.key, spec) })),
    }));
    const lines = metricSeries.map((metric) => `<path data-korea-earnings-line="${metric.key}" d="${linePath(metric.points, domain.min, domain.max, chartWidth, spec.height, padding)}" class="korea-earnings-line korea-earnings-line--${spec.kind} korea-earnings-line--${metric.className}"/>`).join('');
    const dots = metricSeries.flatMap((metric) => metric.points.map((point, index) => Number.isFinite(point.value)
      ? `<circle data-korea-earnings-point="${metric.key}" data-point-index="${index}" cx="${x(index)}" cy="${y(point.value)}" r="${spec.kind === 'amount' ? 2.8 : 2.4}" class="korea-earnings-point korea-earnings-point--${metric.className}"/>` : '')).join('');
    const periodCursor = spec.showPeriodLabels
      ? `<text data-korea-earnings-cursor-period x="0" y="${spec.height - 8}" text-anchor="middle" class="korea-earnings-cursor-period"></text>`
      : '';
    container.innerHTML = `<div class="korea-earnings-chart-layout"><svg class="korea-earnings-y-axis" style="height:${spec.height}px" viewBox="0 0 ${AXIS_WIDTH} ${spec.height}" aria-hidden="true">${axis}</svg><div class="korea-earnings-chart-frame"><svg class="korea-earnings-chart-svg" width="${chartWidth}" height="${spec.height}" viewBox="0 0 ${chartWidth} ${spec.height}" role="img" aria-label="영업이익·순이익 ${spec.kind} 시계열">${grids}${labels}${lines}${dots}<line data-korea-earnings-cursor x1="0" y1="${padding.top}" x2="0" y2="${spec.height - padding.bottom}" class="korea-earnings-cursor"/><text data-korea-earnings-cursor-label x="0" y="15" text-anchor="middle" class="korea-earnings-cursor-label"></text>${periodCursor}<rect x="0" y="0" width="${chartWidth}" height="${spec.height}" fill="transparent" data-korea-earnings-hit/></svg></div></div>`;
    const frame = container.querySelector('.korea-earnings-chart-frame'), hit = container.querySelector('[data-korea-earnings-hit]');
    const cursor = container.querySelector('[data-korea-earnings-cursor]'), cursorLabel = container.querySelector('[data-korea-earnings-cursor-label]');
    const cursorPeriod = container.querySelector('[data-korea-earnings-cursor-period]');
    const yLabels = [...container.querySelectorAll('[data-korea-earnings-y-label]')];
    const yGrids = [...container.querySelectorAll('[data-korea-earnings-y-grid]')];
    const lineElements = new Map(METRICS.map((metric) => [metric.key, container.querySelector(`[data-korea-earnings-line="${metric.key}"]`)]));
    const pointElements = [...container.querySelectorAll('[data-korea-earnings-point]')];
    const indexFromEvent = (event) => {
      const rect = hit.getBoundingClientRect(), localX = (event.clientX - rect.left) * (chartWidth / rect.width);
      return Math.max(0, Math.min(points.length - 1, Math.round(scale(localX, padding.left, chartWidth - padding.right, 0, Math.max(points.length - 1, 1)))));
    };
    const showCursor = (index) => {
      const point = points[index], cursorX = x(index);
      const details = chartMetrics.map((metric) => {
        return `${metric.label} ${formatChartValue(point, metric, spec)}`;
      }).join(' · ');
      const labelX = Math.max(170, Math.min(chartWidth - 170, cursorX));
      cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX); cursorLabel.setAttribute('x', labelX);
      cursorLabel.textContent = details;
      cursor.classList.add('is-visible'); cursorLabel.classList.add('is-visible');
      if (cursorPeriod) {
        cursorPeriod.setAttribute('x', Math.max(32, Math.min(chartWidth - 32, cursorX)));
        cursorPeriod.textContent = periodLabel(point);
        cursorPeriod.classList.add('is-visible');
      }
    };
    const hideCursor = () => {
      cursor.classList.remove('is-visible'); cursorLabel.classList.remove('is-visible');
      cursorPeriod?.classList.remove('is-visible');
    };
    // 스크롤로 실제 보이는 분기만 기준으로 Y축을 다시 잡습니다. 과거의
    // 큰 이상치가 현재 화면의 정상적인 진폭을 눌러버리지 않게 합니다.
    let scaleFrame = null;
    const updateVisibleScale = () => {
      scaleFrame = null;
      const visibleStart = frame.scrollLeft, visibleEnd = visibleStart + frame.clientWidth;
      const visible = points.filter((_, index) => {
        const pointX = x(index);
        return pointX >= visibleStart - 1 && pointX <= visibleEnd + 1;
      });
      if (!visible.length) return;
      const visibleDomain = domainFor(visible);
      yLabels.forEach((label, index) => {
        const value = visibleDomain.ticks[index];
        label.setAttribute('y', y(value, visibleDomain) + 3);
        label.textContent = formatAxis(value, spec.kind);
      });
      yGrids.forEach((grid, index) => {
        const value = visibleDomain.ticks[index], gridY = y(value, visibleDomain);
        grid.setAttribute('y1', gridY); grid.setAttribute('y2', gridY);
        grid.classList.toggle('korea-earnings-grid--zero', Math.abs(value) < Number.EPSILON);
      });
      metricSeries.forEach((metric) => lineElements.get(metric.key)?.setAttribute(
        'd', linePath(metric.points, visibleDomain.min, visibleDomain.max, chartWidth, spec.height, padding),
      ));
      pointElements.forEach((point) => {
        const metricKey = point.dataset.koreaEarningsPoint;
        const value = metricSeries.find((metric) => metric.key === metricKey)?.points[Number(point.dataset.pointIndex)]?.value;
        if (Number.isFinite(value)) point.setAttribute('cy', y(value, visibleDomain));
      });
    };
    frame.addEventListener('scroll', () => {
      if (scaleFrame !== null) return;
      scaleFrame = window.requestAnimationFrame(updateVisibleScale);
    }, { passive: true });
    window.MacroWatchAnalysisChart.scrollToLatest(frame);
    window.requestAnimationFrame(updateVisibleScale);
    return { frame, hit, indexFromEvent, showCursor, hideCursor, updateVisibleScale };
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

  // 어느 보조차트에 마우스를 두더라도 같은 분기의 세로선과 각 차트 값을 함께 표시합니다.
  function synchronizeCursors(charts) {
    charts.forEach((chart) => chart.hit.addEventListener('pointermove', (event) => {
      const index = chart.indexFromEvent(event);
      charts.forEach((target) => target.showCursor(index));
    }));
    const stack = document.querySelector('.korea-earnings-chart-stack');
    if (stack) stack.onpointerleave = () => charts.forEach((chart) => chart.hideCursor());
  }

  function setStatus(message) {
    CHARTS.forEach((chart) => {
      const container = document.getElementById(chart.id);
      if (container) container.innerHTML = `<div class="analysis-empty-state-light flex min-h-40 items-center justify-center border border-dashed p-5 text-sm text-slate-500">${message}</div>`;
    });
  }

  function render() {
    const points = visiblePoints();
    if (!points.length) { setStatus('비교 가능한 KOSPI 시총 상위기업 실적이 아직 없습니다.'); return; }
    updateSummary(points);
    const charts = CHARTS.map((chart) => renderChart(chart, points)).filter(Boolean);
    synchronizeFrames(charts.map((chart) => chart.frame));
    synchronizeCursors(charts);
  }

  async function load({ supabaseClient }) {
    if (!document.getElementById('korea-earnings-amount-chart') || !supabaseClient) return;
    const response = await supabaseClient.rpc('earnings_v2_public_market_series', { p_market_id: 'kr_largecap' });
    if (response.error) { setStatus('KOSPI 100 집계 실적을 불러오지 못했습니다.'); return; }
    state.series = seriesFromMarketRows(response.data || []);
    render();
  }

  document.querySelector('[data-korea-earnings-ranges]')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-korea-earnings-range]');
    if (!button) return;
    state.years = button.dataset.koreaEarningsRange === 'max' ? 'max' : Number(button.dataset.koreaEarningsRange);
    document.querySelectorAll('[data-korea-earnings-range]').forEach((item) => item.classList.toggle('is-active', item === button));
    render();
  });
  // 전용 기업 이익 메뉴가 표시된 뒤 숨김 상태에서 계산한 차트 폭을 다시 맞춥니다.
  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => { if (detail?.view === 'earnings') render(); });
  window.MacroWatchKoreaEarnings = Object.freeze({ seriesFromMarketRows, axisDomain });
  window.MacroWatchDashboard?.registerLoader(load);
})();

