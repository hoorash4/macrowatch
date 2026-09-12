(() => {
  'use strict';

  const HEIGHT = window.MacroWatchAnalysisChart.chartLayout.mainHeight;
  const MIN_VIEWPORT_WIDTH = window.MacroWatchAnalysisChart.chartLayout.mobileMinWidth;
  const Y_AXIS_WIDTH = window.MacroWatchAnalysisChart.chartLayout.axisWidth;
  const PADDING = window.MacroWatchAnalysisChart.plotPadding();
  const chartUtils = window.MacroWatchAnalysisChart;
  const PROFILE = chartUtils.chartProfile({ xAxisMode: 'zero', cursorSeries: Object.freeze([{ key: 'fiveDayAverage', label: '이머징 자금여건' }]) });
  const state = { rows: [], selectedYears: PROFILE.defaultYears };
  const scale = (value, sourceMin, sourceMax, targetMin, targetMax) => sourceMax === sourceMin
    ? (targetMin + targetMax) / 2
    : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);

  function formatMonthDay(period) {
    const [, month, day] = String(period).split('-').map(Number);
    return `${month}월 ${day}일`;
  }

  function withFiveDayAverage(rows) {
    return rows.map((row, index) => {
      if (index < 4) return { ...row, fiveDayAverage: null };
      const values = rows.slice(index - 4, index + 1).map((item) => Number(item.capacity_index));
      return { ...row, fiveDayAverage: values.reduce((sum, value) => sum + value, 0) / values.length };
    });
  }

  function verticalScale(points) {
    const values = points.flatMap((point) => [point.value, point.fiveDayAverage]).filter(Number.isFinite);
    const domain = chartUtils.axisDomain(values, { symmetric: true, minimumSpan: .2 })
      || { min: -.125, max: .125, ticks: [-.1, -.05, 0, .05, .1], step: .05 };
    return domain;
  }

  function render(container, rows, selectedYears) {
    const points = withFiveDayAverage(rows).map((row) => ({
      ...row, timestamp: Date.parse(`${row.observation_date}T00:00:00Z`), value: Number(row.capacity_index),
    })).filter((row) => Number.isFinite(row.timestamp) && Number.isFinite(row.value));
    if (!points.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">이머징 자금 유입 여건 데이터가 아직 없습니다.</div>';
      return;
    }
    const firstTimestamp = points[0].timestamp;
    const lastTimestamp = points[points.length - 1].timestamp;
    const viewportWidth = Math.max(MIN_VIEWPORT_WIDTH, (container.clientWidth || MIN_VIEWPORT_WIDTH) - Y_AXIS_WIDTH);
    const timelineWidth = chartUtils.timelineWidth(viewportWidth, firstTimestamp, lastTimestamp, selectedYears);
    points.forEach((point) => { point.x = scale(point.timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right); });
    const initialScale = verticalScale(points);
    const pathFor = (key, sourceDomain, sourcePoints = points) => window.MacroWatchAnalysisChart.monotonePath(sourcePoints
      .filter((point) => Number.isFinite(point[key]))
      .map((point) => ({
        x: point.x,
        y: scale(point[key], sourceDomain.min, sourceDomain.max, HEIGHT - PADDING.bottom, PADDING.top),
      })));
    const firstYear = new Date(firstTimestamp).getUTCFullYear();
    const firstProvisionalIndex = points.findIndex((point) => point.is_provisional);
    const confirmedPoints = firstProvisionalIndex < 0 ? points : points.slice(0, firstProvisionalIndex);
    const provisionalPoints = firstProvisionalIndex < 0 ? [] : points.slice(Math.max(0, firstProvisionalIndex - 1));
    const lastYear = new Date(lastTimestamp).getUTCFullYear();
    const yearGuides = Array.from({ length: lastYear - firstYear + 1 }, (_, index) => {
      const year = firstYear + index;
      const timestamp = Date.UTC(year, 0, 1);
      if (timestamp < firstTimestamp || timestamp > lastTimestamp) return '';
      const x = scale(timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right);
      const label = selectedYears === 'max' ? String(year).slice(-2) : String(year);
      return `<line x1="${x}" y1="${PADDING.top}" x2="${x}" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-year-guide"/><text x="${x}" y="${HEIGHT - 10}" text-anchor="middle" class="policy-expectation-year">${label}</text>`;
    }).join('');
    const yPosition = (value, sourceDomain = initialScale) => scale(value, sourceDomain.min, sourceDomain.max, HEIGHT - PADDING.bottom, PADDING.top);
    const tickSlots = Array.from({ length: 6 }, (_, index) => index);
    const grids = tickSlots.map((index) => { const value = initialScale.ticks[index], y = Number.isFinite(value) ? yPosition(value) : PADDING.top; return `<line data-em-capacity-y-grid="${index}" x1="${PADDING.left}" y1="${y}" x2="${timelineWidth - PADDING.right}" y2="${y}" class="policy-expectation-y-grid${value === 0 ? ' analysis-chart-zero-line' : ''}"${Number.isFinite(value) ? '' : ' visibility="hidden"'}/>`; }).join('');
    const labels = tickSlots.map((index) => { const value = initialScale.ticks[index], y = Number.isFinite(value) ? yPosition(value) : PADDING.top; return `<line data-em-capacity-y-tick="${index}" x1="${Y_AXIS_WIDTH - 5}" y1="${y}" x2="${Y_AXIS_WIDTH}" y2="${y}" class="policy-expectation-y-tick"${Number.isFinite(value) ? '' : ' visibility="hidden"'}/><text data-em-capacity-y-index="${index}" x="${Y_AXIS_WIDTH - 9}" y="${y + 3}" text-anchor="end" class="policy-expectation-y-label"${Number.isFinite(value) ? '' : ' visibility="hidden"'}>${Number.isFinite(value) ? chartUtils.formatAxisNumber(value) : ''}</text>`; }).join('');
    const { frame, svg } = chartUtils.mountChartFrame({ container, profile: PROFILE, height: HEIGHT, axisViewWidth: Y_AXIS_WIDTH, leftAxisMarkup: labels, ariaLabel: '이머징 자금 유입 여건', plotMarkup: `<svg class="policy-expectation-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="0선을 중심으로 표시한 이머징 자금 유입 여건"><defs><linearGradient id="em-capacity-line-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}"><stop offset="0%" stop-color="#b4535d"/><stop offset="50%" stop-color="#b4535d"/><stop offset="50%" stop-color="#2563a8"/><stop offset="100%" stop-color="#2563a8"/></linearGradient></defs><g>${yearGuides}</g><g>${grids}</g><text x="${PADDING.left + 4}" y="${yPosition(0) - 7}" class="policy-expectation-zero-label">평균적 유입 여건</text><path d="${pathFor('value', initialScale)}" class="policy-expectation-line policy-expectation-line--raw" style="stroke:url(#em-capacity-line-gradient)"/><path d="${pathFor('fiveDayAverage', initialScale, confirmedPoints)}" class="policy-expectation-line policy-expectation-line--average" style="stroke:url(#em-capacity-line-gradient)"/><path d="${pathFor('fiveDayAverage', initialScale, provisionalPoints)}" class="policy-expectation-line policy-expectation-line--average em-capacity-line--provisional" style="stroke:#6b7280"/><line data-em-capacity-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/><text data-em-capacity-value x="0" y="16" text-anchor="middle" class="analysis-chart-cursor-text analysis-chart-cursor-value" visibility="hidden"></text><text data-em-capacity-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 12}" class="analysis-chart-cursor-text analysis-chart-cursor-date" visibility="hidden"></text></svg>` });
    const rawLine = container.querySelector('.policy-expectation-line--raw');
    const averageLine = container.querySelector('.policy-expectation-line--average');
    const provisionalLine = container.querySelector('.em-capacity-line--provisional');
    const yLabels = [...container.querySelectorAll('[data-em-capacity-y-index]')];
    const yGrids = [...container.querySelectorAll('[data-em-capacity-y-grid]')];
    const cursor = container.querySelector('[data-em-capacity-cursor]');
    const detail = container.querySelector('[data-em-capacity-detail]');
    const cursorValue = container.querySelector('[data-em-capacity-value]');
    let animationFrame = null;
    const updateVisibleScale = () => {
      animationFrame = null;
      const visiblePoints = points.filter((point) => point.x >= frame.scrollLeft && point.x <= frame.scrollLeft + frame.clientWidth);
      if (!visiblePoints.length) return;
      const current = verticalScale(visiblePoints);
      rawLine.setAttribute('d', pathFor('value', current));
      averageLine.setAttribute('d', pathFor('fiveDayAverage', current, confirmedPoints));
      provisionalLine.setAttribute('d', pathFor('fiveDayAverage', current, provisionalPoints));
      yLabels.forEach((label) => {
        const index = Number(label.dataset.emCapacityYIndex), value = current.ticks[index];
        const tick = container.querySelector(`[data-em-capacity-y-tick="${index}"]`), grid = yGrids[index];
        if (!Number.isFinite(value)) { label.setAttribute('visibility', 'hidden'); tick?.setAttribute('visibility', 'hidden'); grid?.setAttribute('visibility', 'hidden'); return; }
        const y = yPosition(value, current);
        label.removeAttribute('visibility'); tick?.removeAttribute('visibility'); grid?.removeAttribute('visibility');
        label.textContent = chartUtils.formatAxisNumber(value, { showPlus: true });
        label.setAttribute('y', y + 3); tick?.setAttribute('y1', y); tick?.setAttribute('y2', y); grid?.setAttribute('y1', y); grid?.setAttribute('y2', y); grid?.classList.toggle('analysis-chart-zero-line', value === 0);
      });
    };
    frame.addEventListener('scroll', () => {
      if (animationFrame === null) animationFrame = window.requestAnimationFrame(updateVisibleScale);
    }, { passive: true });
    chartUtils.scrollToLatest(frame);
    window.requestAnimationFrame(updateVisibleScale);
    frame.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = ((event.clientX - bounds.left) / bounds.width) * timelineWidth;
      const nearest = points.reduce((closest, point) => Math.abs(point.x - pointerX) < Math.abs(closest.x - pointerX) ? point : closest);
      cursor.setAttribute('x1', nearest.x); cursor.setAttribute('x2', nearest.x);
      detail.textContent = formatMonthDay(nearest.observation_date); detail.setAttribute('visibility', 'visible');
      cursorValue.textContent = chartUtils.cursorValueText(nearest, PROFILE.cursorSeries); cursorValue.setAttribute('visibility', 'visible'); chartUtils.positionCursorText(cursorValue, nearest.x, frame); chartUtils.positionCursorText(detail, nearest.x, frame);
      cursor.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); detail.setAttribute('visibility', 'hidden');
      cursorValue.setAttribute('visibility', 'hidden'); });
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('em-capacity-chart');
    if (!container || !supabaseClient) return;
    const { data, error } = await supabaseClient.from('em_capital_capacity_daily').select('observation_date,capacity_index,is_provisional').order('observation_date');
    if (error) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">이머징 자금 유입 여건을 불러오지 못했습니다.</div>';
      return;
    }
    state.rows = data || [];
    render(container, state.rows, state.selectedYears);
    const controls = document.querySelector('[data-em-capacity-ranges]');
    if (controls && controls.dataset.bound !== 'true') {
      controls.dataset.bound = 'true';
      controls.addEventListener('click', (event) => {
        const button = event.target.closest('[data-em-capacity-range]');
        if (!button) return;
        state.selectedYears = button.dataset.emCapacityRange === 'max' ? 'max' : Number(button.dataset.emCapacityRange);
        controls.querySelectorAll('[data-em-capacity-range]').forEach((item) => item.classList.toggle('is-active', item === button));
        render(container, state.rows, state.selectedYears);
      });
    }
  }

  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => {
    if (detail?.view !== 'stress' || detail?.stressMarket !== 'em') return;
    chartUtils.scrollToLatest(document.querySelector('#em-capacity-chart [data-history-scroll]'));
  });
  window.MacroWatchDashboard?.registerLoader(load);
})();
