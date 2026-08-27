(() => {
  'use strict';

  const HEIGHT = 320;
  const MIN_VIEWPORT_WIDTH = 680;
  const Y_AXIS_WIDTH = 46;
  const PADDING = { top: 28, right: 24, bottom: 42, left: 12 };
  const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000;
  const Z_WINDOW = 756;
  const MIN_Z_HISTORY = 60;
  const EWMA_ALPHA = 2 / 11;
  const state = { rows: [], standardYears: 1, cumulativeYears: 1, ewmaYears: 1 };
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

  function render(container, rows, selectedYears, gradientId, showAverage = true) {
    const chartRows = showAverage ? withFiveDayAverage(rows) : rows.map((row) => ({ ...row, fiveDayAverage: null }));
    const points = chartRows.map((row) => ({
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
    const primaryLineClass = showAverage ? 'policy-expectation-line--raw' : 'policy-expectation-line--average';
    container.innerHTML = `<div class="policy-expectation-chart-layout"><svg class="policy-expectation-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${labels}</svg><div class="policy-expectation-chart-frame"><svg class="policy-expectation-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="0선을 중심으로 표시한 한국 외국인 자금 유출입 강도"><defs><linearGradient id="${gradientId}" gradientUnits="userSpaceOnUse" x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}"><stop offset="0%" stop-color="#b4535d"/><stop offset="50%" stop-color="#b4535d"/><stop offset="50%" stop-color="#2563a8"/><stop offset="100%" stop-color="#2563a8"/></linearGradient></defs><g>${yearGuides}</g><g>${grids}</g><text x="${PADDING.left + 4}" y="${yPosition(0) - 7}" class="policy-expectation-zero-label">평균적 유입 여건</text><path data-korea-primary-line d="${pathFor('value', initialScale.maximumAbsoluteValue)}" class="policy-expectation-line ${primaryLineClass}" style="stroke:url(#${gradientId})"/><path data-korea-average-line d="${showAverage ? pathFor('fiveDayAverage', initialScale.maximumAbsoluteValue, confirmedPoints) : ''}" class="policy-expectation-line policy-expectation-line--average" style="stroke:url(#${gradientId})"/><path data-korea-provisional-line d="${showAverage ? pathFor('fiveDayAverage', initialScale.maximumAbsoluteValue, provisionalPoints) : ''}" class="policy-expectation-line policy-expectation-line--average korea-foreign-flow-line--provisional" style="stroke:#6b7280"/><line data-korea-foreign-flow-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/><text data-korea-foreign-flow-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-expectation-cursor-detail"></text></svg></div></div>`;
    const frame = container.querySelector('.policy-expectation-chart-frame');
    const svg = container.querySelector('.policy-expectation-chart-svg');
    const rawLine = container.querySelector('[data-korea-primary-line]');
    const averageLine = container.querySelector('[data-korea-average-line]');
    const provisionalLine = container.querySelector('[data-korea-provisional-line]');
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
      if (showAverage) {
        averageLine.setAttribute('d', pathFor('fiveDayAverage', current.maximumAbsoluteValue, confirmedPoints));
        provisionalLine.setAttribute('d', pathFor('fiveDayAverage', current.maximumAbsoluteValue, provisionalPoints));
      }
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

  function mean(values) { return values.reduce((sum, value) => sum + value, 0) / values.length; }

  // 각 날짜에는 그날까지 공개된 최근 3년 자료만 사용하여 미래 정보가 섞이지 않게 합니다.
  function causalZScore(values, index) {
    const history = values.slice(Math.max(0, index - Z_WINDOW + 1), index + 1).filter(Number.isFinite);
    if (history.length < MIN_Z_HISTORY || !Number.isFinite(values[index])) return null;
    const average = mean(history);
    const variance = mean(history.map((value) => (value - average) ** 2));
    return variance > 0 ? Math.max(-3, Math.min(3, (values[index] - average) / Math.sqrt(variance))) : 0;
  }

  function calculateCumulativeRows(rows) {
    const flowValues = [], wonValues = [];
    return rows.flatMap((row, index) => {
      if (index < 10) { flowValues.push(Number.NaN); wonValues.push(Number.NaN); return []; }
      const windowRows = rows.slice(index - 9, index + 1);
      const netBuy = windowRows.reduce((sum, item) => sum + Number(item.foreign_net_buy_amount), 0);
      const tradingValue = windowRows.reduce((sum, item) => sum + Number(item.kospi_trading_value), 0);
      const previousRate = Number(rows[index - 10].usdkrw_rate);
      const currentRate = Number(row.usdkrw_rate);
      flowValues.push(tradingValue > 0 ? netBuy / tradingValue : Number.NaN);
      wonValues.push(previousRate > 0 ? -(currentRate / previousRate - 1) : Number.NaN);
      const flowZ = causalZScore(flowValues, index), wonZ = causalZScore(wonValues, index);
      return flowZ === null || wonZ === null ? [] : [{ ...row, flow_index: (flowZ + wonZ) / 2 }];
    });
  }

  function calculateEwmaRows(rows) {
    const flowEwma = [], wonEwma = [];
    let previousFlow = null, previousWon = null, regime = 'neutral';
    return rows.flatMap((row, index) => {
      const ratio = Number(row.kospi_trading_value) > 0 ? Number(row.foreign_net_buy_amount) / Number(row.kospi_trading_value) : Number.NaN;
      const previousRate = index ? Number(rows[index - 1].usdkrw_rate) : Number.NaN;
      const wonStrength = previousRate > 0 ? -(Number(row.usdkrw_rate) / previousRate - 1) : Number.NaN;
      previousFlow = Number.isFinite(ratio) ? (previousFlow === null ? ratio : EWMA_ALPHA * ratio + (1 - EWMA_ALPHA) * previousFlow) : previousFlow;
      previousWon = Number.isFinite(wonStrength) ? (previousWon === null ? wonStrength : EWMA_ALPHA * wonStrength + (1 - EWMA_ALPHA) * previousWon) : previousWon;
      flowEwma.push(previousFlow ?? Number.NaN); wonEwma.push(previousWon ?? Number.NaN);
      const flowZ = causalZScore(flowEwma, index), wonZ = causalZScore(wonEwma, index);
      if (flowZ === null || wonZ === null) return [];
      const value = (flowZ + wonZ) / 2;
      if (regime === 'neutral') {
        if (value >= 0.4) regime = 'strengthening';
        else if (value <= -0.4) regime = 'weakening';
      } else if (regime === 'strengthening' && value <= 0.15) regime = value <= -0.4 ? 'weakening' : 'neutral';
      else if (regime === 'weakening' && value >= -0.15) regime = value >= 0.4 ? 'strengthening' : 'neutral';
      return [{ ...row, flow_index: value, regime }];
    });
  }

  function bindRangeControls({ controls, buttonSelector, datasetKey, stateKey, container, rows, gradientId, showAverage }) {
    if (!controls || controls.dataset.bound === 'true') return;
    controls.dataset.bound = 'true';
    controls.addEventListener('click', (event) => {
      const button = event.target.closest(buttonSelector);
      if (!button) return;
      const range = button.dataset[datasetKey];
      state[stateKey] = range === 'max' ? 'max' : Number(range);
      controls.querySelectorAll(buttonSelector).forEach((item) => item.classList.toggle('is-active', item === button));
      render(container, rows, state[stateKey], gradientId, showAverage);
    });
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('korea-foreign-flow-chart');
    const cumulativeContainer = document.getElementById('korea-foreign-flow-cumulative-chart');
    const ewmaContainer = document.getElementById('korea-foreign-flow-ewma-chart');
    if (!container || !supabaseClient) return;
    const { data, error } = await chartUtils.loadAllRows((from, to) => supabaseClient
      .from('korea_foreign_flow_daily')
      .select('observation_date,foreign_net_buy_amount,kospi_trading_value,usdkrw_rate,won_strength_z,flow_index')
      .order('observation_date')
      .range(from, to));
    if (error) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">한국 외국인 자금 유출입 강도을 불러오지 못했습니다.</div>';
      return;
    }
    state.rows = data || [];
    const cumulativeRows = calculateCumulativeRows(state.rows);
    const ewmaRows = calculateEwmaRows(state.rows);
    render(container, state.rows, state.standardYears, 'korea-foreign-flow-line-gradient');
    if (cumulativeContainer) render(cumulativeContainer, cumulativeRows, state.cumulativeYears, 'korea-foreign-flow-cumulative-gradient', false);
    if (ewmaContainer) render(ewmaContainer, ewmaRows, state.ewmaYears, 'korea-foreign-flow-ewma-gradient', false);
    const latestRegime = ewmaRows.at(-1)?.regime || 'neutral';
    const regimeElement = document.getElementById('korea-foreign-flow-ewma-state');
    if (regimeElement) {
      regimeElement.textContent = latestRegime === 'strengthening' ? '강화' : latestRegime === 'weakening' ? '약화' : '중립';
      regimeElement.className = `font-semibold ${latestRegime === 'strengthening' ? 'text-rose-700' : latestRegime === 'weakening' ? 'text-blue-700' : 'text-slate-600'}`;
    }
    bindRangeControls({ controls: document.querySelector('[data-korea-foreign-flow-ranges]'), buttonSelector: '[data-korea-foreign-flow-range]', datasetKey: 'koreaForeignFlowRange', stateKey: 'standardYears', container, rows: state.rows, gradientId: 'korea-foreign-flow-line-gradient', showAverage: true });
    bindRangeControls({ controls: document.querySelector('[data-korea-foreign-flow-cumulative-ranges]'), buttonSelector: '[data-korea-foreign-flow-cumulative-range]', datasetKey: 'koreaForeignFlowCumulativeRange', stateKey: 'cumulativeYears', container: cumulativeContainer, rows: cumulativeRows, gradientId: 'korea-foreign-flow-cumulative-gradient', showAverage: false });
    bindRangeControls({ controls: document.querySelector('[data-korea-foreign-flow-ewma-ranges]'), buttonSelector: '[data-korea-foreign-flow-ewma-range]', datasetKey: 'koreaForeignFlowEwmaRange', stateKey: 'ewmaYears', container: ewmaContainer, rows: ewmaRows, gradientId: 'korea-foreign-flow-ewma-gradient', showAverage: false });
  }

  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => {
    if (detail?.view !== 'korea') return;
    chartUtils.scrollToLatest(document.querySelector('#korea-foreign-flow-chart .policy-expectation-chart-frame'));
    chartUtils.scrollToLatest(document.querySelector('#korea-foreign-flow-cumulative-chart .policy-expectation-chart-frame'));
    chartUtils.scrollToLatest(document.querySelector('#korea-foreign-flow-ewma-chart .policy-expectation-chart-frame'));
  });
  window.MacroWatchDashboard?.registerLoader(load);
})();

