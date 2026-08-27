(() => {
  'use strict';

  const HEIGHT = 320;
  const MIN_VIEWPORT_WIDTH = 680;
  const Y_AXIS_WIDTH = 46;
  const PADDING = { top: 28, right: 24, bottom: 42, left: 12 };
  const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000;
  const state = { rows: [], selectedYears: 1 };
  const chartUtils = window.MacroWatchAnalysisChart;
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
    const maximum = Math.max(0.1, ...points.flatMap((point) => [
      Math.abs(point.value), Number.isFinite(point.fiveDayAverage) ? Math.abs(point.fiveDayAverage) : 0,
    ])) * 1.05;
    const tickStep = chartUtils.niceStep(maximum / 2);
    return { tickStep, maximumAbsoluteValue: tickStep * 2 };
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
    const timelineWidth = selectedYears === 'max'
      ? viewportWidth
      : Math.max(viewportWidth, viewportWidth * ((lastTimestamp - firstTimestamp) / (Number(selectedYears) * YEAR_MS)));
    points.forEach((point) => { point.x = scale(point.timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right); });
    const initialScale = verticalScale(points);
    const pathFor = (key, maximum, sourcePoints = points) => sourcePoints.filter((point) => Number.isFinite(point[key])).map((point, index) => {
      const y = scale(point[key], -maximum, maximum, HEIGHT - PADDING.bottom, PADDING.top);
      return `${index ? 'L' : 'M'} ${point.x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(' ');
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
    const tickMultiples = [-2, -1, 0, 1, 2];
    const yPosition = (multiple) => scale(multiple, -2, 2, HEIGHT - PADDING.bottom, PADDING.top);
    const grids = tickMultiples.map((multiple) => `<line x1="${PADDING.left}" y1="${yPosition(multiple)}" x2="${timelineWidth - PADDING.right}" y2="${yPosition(multiple)}" class="policy-expectation-y-grid${multiple === 0 ? ' policy-expectation-y-grid--zero' : ''}"/>`).join('');
    const labels = tickMultiples.map((multiple) => `<line x1="${Y_AXIS_WIDTH - 5}" y1="${yPosition(multiple)}" x2="${Y_AXIS_WIDTH}" y2="${yPosition(multiple)}" class="policy-expectation-y-tick"/><text data-em-capacity-y-multiple="${multiple}" x="${Y_AXIS_WIDTH - 9}" y="${yPosition(multiple) + 3}" text-anchor="end" class="policy-expectation-y-label">${Number((multiple * initialScale.tickStep).toFixed(2))}</text>`).join('');
    container.innerHTML = `<div class="policy-expectation-chart-layout"><svg class="policy-expectation-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${labels}</svg><div class="policy-expectation-chart-frame"><svg class="policy-expectation-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="0선을 중심으로 표시한 이머징 자금 유입 여건"><defs><linearGradient id="em-capacity-line-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}"><stop offset="0%" stop-color="#b4535d"/><stop offset="50%" stop-color="#b4535d"/><stop offset="50%" stop-color="#2563a8"/><stop offset="100%" stop-color="#2563a8"/></linearGradient></defs><g>${yearGuides}</g><g>${grids}</g><text x="${PADDING.left + 4}" y="${yPosition(0) - 7}" class="policy-expectation-zero-label">평균적 유입 여건</text><path d="${pathFor('value', initialScale.maximumAbsoluteValue)}" class="policy-expectation-line policy-expectation-line--raw" style="stroke:url(#em-capacity-line-gradient)"/><path d="${pathFor('fiveDayAverage', initialScale.maximumAbsoluteValue, confirmedPoints)}" class="policy-expectation-line policy-expectation-line--average" style="stroke:url(#em-capacity-line-gradient)"/><path d="${pathFor('fiveDayAverage', initialScale.maximumAbsoluteValue, provisionalPoints)}" class="policy-expectation-line policy-expectation-line--average em-capacity-line--provisional" style="stroke:url(#em-capacity-line-gradient)"/><line data-em-capacity-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/><text data-em-capacity-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-expectation-cursor-detail"></text></svg></div></div>`;
    const frame = container.querySelector('.policy-expectation-chart-frame');
    const svg = container.querySelector('.policy-expectation-chart-svg');
    const rawLine = container.querySelector('.policy-expectation-line--raw');
    const averageLine = container.querySelector('.policy-expectation-line--average');
    const provisionalLine = container.querySelector('.em-capacity-line--provisional');
    const yLabels = [...container.querySelectorAll('[data-em-capacity-y-multiple]')];
    const cursor = container.querySelector('[data-em-capacity-cursor]');
    const detail = container.querySelector('[data-em-capacity-detail]');
    let animationFrame = null;
    const updateVisibleScale = () => {
      animationFrame = null;
      const visiblePoints = points.filter((point) => point.x >= frame.scrollLeft && point.x <= frame.scrollLeft + frame.clientWidth);
      if (!visiblePoints.length) return;
      const current = verticalScale(visiblePoints);
      rawLine.setAttribute('d', pathFor('value', current.maximumAbsoluteValue));
      averageLine.setAttribute('d', pathFor('fiveDayAverage', current.maximumAbsoluteValue, confirmedPoints));
      provisionalLine.setAttribute('d', pathFor('fiveDayAverage', current.maximumAbsoluteValue, provisionalPoints));
      yLabels.forEach((label) => {
        const value = Number(label.dataset.emCapacityYMultiple) * current.tickStep;
        label.textContent = `${value > 0 ? '+' : ''}${Number(value.toFixed(2))}`;
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
      detail.setAttribute('x', nearest.x); detail.textContent = formatMonthDay(nearest.observation_date);
      cursor.classList.add('is-visible'); detail.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); detail.classList.remove('is-visible'); });
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
    if (detail?.view !== 'em') return;
    chartUtils.scrollToLatest(document.querySelector('#em-capacity-chart .policy-expectation-chart-frame'));
  });
  window.MacroWatchDashboard?.registerLoader(load);
})();
