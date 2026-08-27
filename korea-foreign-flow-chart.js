(() => {
  'use strict';

  const HEIGHT = 320;
  const MIN_VIEWPORT_WIDTH = 680;
  const Y_AXIS_WIDTH = 46;
  const PADDING = { top: 28, right: 24, bottom: 42, left: 12 };
  const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000;
  const state = { rows: [], standardYears: 1, persistenceYears: 1 };
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
      const values = rows.slice(index - 4, index + 1).map((item) => Number(item.flow_index));
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

  function render(container, rows, selectedYears, gradientId) {
    const points = withFiveDayAverage(rows).map((row) => ({
      ...row, timestamp: Date.parse(`${row.observation_date}T00:00:00Z`), value: Number(row.flow_index),
    })).filter((row) => Number.isFinite(row.timestamp) && Number.isFinite(row.value));
    if (!points.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">한국 외국인 자금 유출입 강도 데이터가 아직 없습니다.</div>';
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
    const firstProvisionalIndex = -1;
    const confirmedPoints = points;
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
    const labels = tickMultiples.map((multiple) => `<line x1="${Y_AXIS_WIDTH - 5}" y1="${yPosition(multiple)}" x2="${Y_AXIS_WIDTH}" y2="${yPosition(multiple)}" class="policy-expectation-y-tick"/><text data-korea-foreign-flow-y-multiple="${multiple}" x="${Y_AXIS_WIDTH - 9}" y="${yPosition(multiple) + 3}" text-anchor="end" class="policy-expectation-y-label">${Number((multiple * initialScale.tickStep).toFixed(2))}</text>`).join('');
    container.innerHTML = `<div class="policy-expectation-chart-layout"><svg class="policy-expectation-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${labels}</svg><div class="policy-expectation-chart-frame"><svg class="policy-expectation-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="0선을 중심으로 표시한 한국 외국인 자금 유출입 강도"><defs><linearGradient id="${gradientId}" gradientUnits="userSpaceOnUse" x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}"><stop offset="0%" stop-color="#b4535d"/><stop offset="50%" stop-color="#b4535d"/><stop offset="50%" stop-color="#2563a8"/><stop offset="100%" stop-color="#2563a8"/></linearGradient></defs><g>${yearGuides}</g><g>${grids}</g><text x="${PADDING.left + 4}" y="${yPosition(0) - 7}" class="policy-expectation-zero-label">평균적 유입 여건</text><path d="${pathFor('value', initialScale.maximumAbsoluteValue)}" class="policy-expectation-line policy-expectation-line--raw" style="stroke:url(#${gradientId})"/><path d="${pathFor('fiveDayAverage', initialScale.maximumAbsoluteValue, confirmedPoints)}" class="policy-expectation-line policy-expectation-line--average" style="stroke:url(#${gradientId})"/><path d="${pathFor('fiveDayAverage', initialScale.maximumAbsoluteValue, provisionalPoints)}" class="policy-expectation-line policy-expectation-line--average korea-foreign-flow-line--provisional" style="stroke:#6b7280"/><line data-korea-foreign-flow-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/><text data-korea-foreign-flow-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-expectation-cursor-detail"></text></svg></div></div>`;
    const frame = container.querySelector('.policy-expectation-chart-frame');
    const svg = container.querySelector('.policy-expectation-chart-svg');
    const rawLine = container.querySelector('.policy-expectation-line--raw');
    const averageLine = container.querySelector('.policy-expectation-line--average');
    const provisionalLine = container.querySelector('.korea-foreign-flow-line--provisional');
    const yLabels = [...container.querySelectorAll('[data-korea-foreign-flow-y-multiple]')];
    const cursor = container.querySelector('[data-korea-foreign-flow-cursor]');
    const detail = container.querySelector('[data-korea-foreign-flow-detail]');
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
        const value = Number(label.dataset.koreaForeignFlowYMultiple) * current.tickStep;
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

  // 같은 방향 사이에 낀 하루짜리 반대 수급 중 앞뒤 규모의 평균 이하인 값만 제거합니다.
  // 실제 순매매액과 원화 강도는 수정하지 않으므로 비교 실험을 언제든 되돌릴 수 있습니다.
  function applyPersistenceFilter(rows) {
    return rows.map((row, index) => {
      if (index === 0 || index === rows.length - 1) return row;
      const previousSign = Math.sign(Number(rows[index - 1].foreign_net_buy_amount));
      const currentSign = Math.sign(Number(row.foreign_net_buy_amount));
      const nextSign = Math.sign(Number(rows[index + 1].foreign_net_buy_amount));
      const surroundedReversal = previousSign !== 0 && previousSign === nextSign && currentSign === -previousSign;
      const surroundingAverage = (Math.abs(Number(rows[index - 1].foreign_net_buy_amount)) + Math.abs(Number(rows[index + 1].foreign_net_buy_amount))) / 2;
      const isMinorReversal = surroundedReversal && Math.abs(Number(row.foreign_net_buy_amount)) <= surroundingAverage;
      if (!isMinorReversal) return row;
      return { ...row, flow_index: Number(row.won_strength_z) / 2, persistence_filtered: true };
    });
  }

  function bindRangeControls({ controls, buttonSelector, stateKey, container, rows, gradientId }) {
    if (!controls || controls.dataset.bound === 'true') return;
    controls.dataset.bound = 'true';
    controls.addEventListener('click', (event) => {
      const button = event.target.closest(buttonSelector);
      if (!button) return;
      const range = button.dataset[stateKey === 'standardYears' ? 'koreaForeignFlowRange' : 'koreaForeignFlowPersistenceRange'];
      state[stateKey] = range === 'max' ? 'max' : Number(range);
      controls.querySelectorAll(buttonSelector).forEach((item) => item.classList.toggle('is-active', item === button));
      render(container, rows, state[stateKey], gradientId);
    });
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('korea-foreign-flow-chart');
    const persistenceContainer = document.getElementById('korea-foreign-flow-persistence-chart');
    if (!container || !supabaseClient) return;
    const { data, error } = await chartUtils.loadAllRows((from, to) => supabaseClient
      .from('korea_foreign_flow_daily')
      .select('observation_date,foreign_net_buy_amount,won_strength_z,flow_index')
      .order('observation_date')
      .range(from, to));
    if (error) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">한국 외국인 자금 유출입 강도을 불러오지 못했습니다.</div>';
      return;
    }
    state.rows = data || [];
    const persistenceRows = applyPersistenceFilter(state.rows);
    render(container, state.rows, state.standardYears, 'korea-foreign-flow-line-gradient');
    if (persistenceContainer) render(persistenceContainer, persistenceRows, state.persistenceYears, 'korea-foreign-flow-persistence-gradient');
    const controls = document.querySelector('[data-korea-foreign-flow-ranges]');
    bindRangeControls({ controls, buttonSelector: '[data-korea-foreign-flow-range]', stateKey: 'standardYears', container, rows: state.rows, gradientId: 'korea-foreign-flow-line-gradient' });
    bindRangeControls({ controls: document.querySelector('[data-korea-foreign-flow-persistence-ranges]'), buttonSelector: '[data-korea-foreign-flow-persistence-range]', stateKey: 'persistenceYears', container: persistenceContainer, rows: persistenceRows, gradientId: 'korea-foreign-flow-persistence-gradient' });
  }

  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => {
    if (detail?.view !== 'korea') return;
    chartUtils.scrollToLatest(document.querySelector('#korea-foreign-flow-chart .policy-expectation-chart-frame'));
    chartUtils.scrollToLatest(document.querySelector('#korea-foreign-flow-persistence-chart .policy-expectation-chart-frame'));
  });
  window.MacroWatchDashboard?.registerLoader(load);
})();

