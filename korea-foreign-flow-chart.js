(() => {
  'use strict';
  const HEIGHT = 320, MIN_VIEWPORT_WIDTH = 680, Y_AXIS_WIDTH = 46;
  const PADDING = { top: 28, right: 24, bottom: 42, left: 12 };
  const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000, Z_WINDOW = 756, MIN_Z_HISTORY = 60;
  const state = { rows: [], selectedYears: 1 };
  const chartUtils = window.MacroWatchAnalysisChart;
  const scale = (value, sourceMin, sourceMax, targetMin, targetMax) => sourceMax === sourceMin ? (targetMin + targetMax) / 2 : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);
  const mean = (values) => values.reduce((sum, value) => sum + value, 0) / values.length;

  // 각 날짜에는 그날까지 공개된 최근 3년 자료만 사용하여 미래 정보가 섞이지 않게 합니다.
  function causalZScore(values, index) {
    const history = values.slice(Math.max(0, index - Z_WINDOW + 1), index + 1).filter(Number.isFinite);
    if (history.length < MIN_Z_HISTORY || !Number.isFinite(values[index])) return null;
    const average = mean(history), variance = mean(history.map((value) => (value - average) ** 2));
    return variance > 0 ? Math.max(-3, Math.min(3, (values[index] - average) / Math.sqrt(variance))) : 0;
  }

  function calculateTenDayCumulative(rows) {
    const flowValues = [], wonValues = [];
    return rows.flatMap((row, index) => {
      if (index < 10) { flowValues.push(Number.NaN); wonValues.push(Number.NaN); return []; }
      const windowRows = rows.slice(index - 9, index + 1);
      const netBuy = windowRows.reduce((sum, item) => sum + Number(item.foreign_net_buy_amount), 0);
      const tradingValue = windowRows.reduce((sum, item) => sum + Number(item.kospi_trading_value), 0);
      const previousRate = Number(rows[index - 10].usdkrw_rate), currentRate = Number(row.usdkrw_rate);
      flowValues.push(tradingValue > 0 ? netBuy / tradingValue : Number.NaN);
      wonValues.push(previousRate > 0 ? -(currentRate / previousRate - 1) : Number.NaN);
      const flowZ = causalZScore(flowValues, index), wonZ = causalZScore(wonValues, index);
      return flowZ === null || wonZ === null ? [] : [{ ...row, flow_index: (flowZ + wonZ) / 2 }];
    });
  }

  // 그래프 값은 바꾸지 않고 강화·중립·약화 상태에만 진입/해제 문턱을 다르게 적용합니다.
  function applyHysteresis(rows) {
    let regime = 'neutral';
    return rows.map((row) => {
      const value = Number(row.flow_index);
      if (regime === 'neutral') {
        if (value >= 0.4) regime = 'strengthening';
        else if (value <= -0.4) regime = 'weakening';
      } else if (regime === 'strengthening' && value <= 0.15) regime = value <= -0.4 ? 'weakening' : 'neutral';
      else if (regime === 'weakening' && value >= -0.15) regime = value >= 0.4 ? 'strengthening' : 'neutral';
      return { ...row, regime };
    });
  }

  function updateRegimeLabel(rows) {
    const regime = rows.at(-1)?.regime || 'neutral', element = document.getElementById('korea-foreign-flow-state');
    if (!element) return;
    element.textContent = regime === 'strengthening' ? '강화' : regime === 'weakening' ? '약화' : '중립';
    element.className = `font-semibold ${regime === 'strengthening' ? 'text-rose-700' : regime === 'weakening' ? 'text-blue-700' : 'text-slate-600'}`;
  }

  function verticalScale(points) {
    const maximum = Math.max(0.1, ...points.map((point) => Math.abs(point.value))) * 1.05;
    const tickStep = chartUtils.niceStep(maximum / 2);
    return { tickStep, maximumAbsoluteValue: tickStep * 2 };
  }

  function render(container, rows, selectedYears) {
    const points = rows.map((row) => ({ ...row, timestamp: Date.parse(`${row.observation_date}T00:00:00Z`), value: Number(row.flow_index) })).filter((row) => Number.isFinite(row.timestamp) && Number.isFinite(row.value));
    if (!points.length) { container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">한국 외국인 자금 유출입 강도 데이터가 아직 없습니다.</div>'; return; }
    const firstTimestamp = points[0].timestamp, lastTimestamp = points.at(-1).timestamp;
    const viewportWidth = Math.max(MIN_VIEWPORT_WIDTH, (container.clientWidth || MIN_VIEWPORT_WIDTH) - Y_AXIS_WIDTH);
    const timelineWidth = selectedYears === 'max' ? viewportWidth : Math.max(viewportWidth, viewportWidth * ((lastTimestamp - firstTimestamp) / (Number(selectedYears) * YEAR_MS)));
    points.forEach((point) => { point.x = scale(point.timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right); });
    const initialScale = verticalScale(points);
    const pathFor = (maximum) => points.map((point, index) => `${index ? 'L' : 'M'} ${point.x.toFixed(2)} ${scale(point.value, -maximum, maximum, HEIGHT - PADDING.bottom, PADDING.top).toFixed(2)}`).join(' ');
    const firstYear = new Date(firstTimestamp).getUTCFullYear(), lastYear = new Date(lastTimestamp).getUTCFullYear();
    const yearGuides = Array.from({ length: lastYear - firstYear + 1 }, (_, index) => {
      const year = firstYear + index, timestamp = Date.UTC(year, 0, 1);
      if (timestamp < firstTimestamp || timestamp > lastTimestamp) return '';
      const x = scale(timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right);
      return `<line x1="${x}" y1="${PADDING.top}" x2="${x}" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-year-guide"/><text x="${x}" y="${HEIGHT - 10}" text-anchor="middle" class="policy-expectation-year">${selectedYears === 'max' ? String(year).slice(-2) : year}</text>`;
    }).join('');
    const tickMultiples = [-2, -1, 0, 1, 2], yPosition = (multiple) => scale(multiple, -2, 2, HEIGHT - PADDING.bottom, PADDING.top);
    const grids = tickMultiples.map((multiple) => `<line x1="${PADDING.left}" y1="${yPosition(multiple)}" x2="${timelineWidth - PADDING.right}" y2="${yPosition(multiple)}" class="policy-expectation-y-grid${multiple === 0 ? ' policy-expectation-y-grid--zero' : ''}"/>`).join('');
    const labels = tickMultiples.map((multiple) => `<line x1="${Y_AXIS_WIDTH - 5}" y1="${yPosition(multiple)}" x2="${Y_AXIS_WIDTH}" y2="${yPosition(multiple)}" class="policy-expectation-y-tick"/><text data-korea-foreign-flow-y-multiple="${multiple}" x="${Y_AXIS_WIDTH - 9}" y="${yPosition(multiple) + 3}" text-anchor="end" class="policy-expectation-y-label">${Number((multiple * initialScale.tickStep).toFixed(2))}</text>`).join('');
    container.innerHTML = `<div class="policy-expectation-chart-layout"><svg class="policy-expectation-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${labels}</svg><div class="policy-expectation-chart-frame"><svg class="policy-expectation-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="0선을 중심으로 표시한 한국 외국인 자금 유출입 강도"><defs><linearGradient id="korea-foreign-flow-line-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}"><stop offset="0%" stop-color="#b4535d"/><stop offset="50%" stop-color="#b4535d"/><stop offset="50%" stop-color="#2563a8"/><stop offset="100%" stop-color="#2563a8"/></linearGradient></defs><g>${yearGuides}</g><g>${grids}</g><text x="${PADDING.left + 4}" y="${yPosition(0) - 7}" class="policy-expectation-zero-label">평균적 유입 여건</text><path data-korea-flow-line d="${pathFor(initialScale.maximumAbsoluteValue)}" class="policy-expectation-line policy-expectation-line--average" style="stroke:url(#korea-foreign-flow-line-gradient)"/><line data-korea-foreign-flow-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/><text data-korea-foreign-flow-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-expectation-cursor-detail"></text></svg></div></div>`;
    const frame = container.querySelector('.policy-expectation-chart-frame'), svg = container.querySelector('.policy-expectation-chart-svg');
    const line = container.querySelector('[data-korea-flow-line]'), yLabels = [...container.querySelectorAll('[data-korea-foreign-flow-y-multiple]')];
    const cursor = container.querySelector('[data-korea-foreign-flow-cursor]'), detail = container.querySelector('[data-korea-foreign-flow-detail]');
    let animationFrame = null;
    const updateVisibleScale = () => {
      animationFrame = null;
      const visiblePoints = points.filter((point) => point.x >= frame.scrollLeft && point.x <= frame.scrollLeft + frame.clientWidth);
      if (!visiblePoints.length) return;
      const current = verticalScale(visiblePoints); line.setAttribute('d', pathFor(current.maximumAbsoluteValue));
      yLabels.forEach((label) => { const value = Number(label.dataset.koreaForeignFlowYMultiple) * current.tickStep; label.textContent = `${value > 0 ? '+' : ''}${Number(value.toFixed(2))}`; });
    };
    frame.addEventListener('scroll', () => { if (animationFrame === null) animationFrame = window.requestAnimationFrame(updateVisibleScale); }, { passive: true });
    chartUtils.scrollToLatest(frame); window.requestAnimationFrame(updateVisibleScale);
    frame.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect(), pointerX = ((event.clientX - bounds.left) / bounds.width) * timelineWidth;
      const nearest = points.reduce((closest, point) => Math.abs(point.x - pointerX) < Math.abs(closest.x - pointerX) ? point : closest);
      cursor.setAttribute('x1', nearest.x); cursor.setAttribute('x2', nearest.x); detail.setAttribute('x', nearest.x);
      const [, month, day] = String(nearest.observation_date).split('-').map(Number); detail.textContent = `${month}월 ${day}일`;
      cursor.classList.add('is-visible'); detail.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); detail.classList.remove('is-visible'); });
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('korea-foreign-flow-chart');
    if (!container || !supabaseClient) return;
    const { data, error } = await chartUtils.loadAllRows((from, to) => supabaseClient.from('korea_foreign_flow_daily').select('observation_date,foreign_net_buy_amount,kospi_trading_value,usdkrw_rate').order('observation_date').range(from, to));
    if (error) { container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">한국 외국인 자금 유출입 강도를 불러오지 못했습니다.</div>'; return; }
    state.rows = applyHysteresis(calculateTenDayCumulative(data || []));
    updateRegimeLabel(state.rows); render(container, state.rows, state.selectedYears);
    const controls = document.querySelector('[data-korea-foreign-flow-ranges]');
    if (controls && controls.dataset.bound !== 'true') {
      controls.dataset.bound = 'true';
      controls.addEventListener('click', (event) => {
        const button = event.target.closest('[data-korea-foreign-flow-range]');
        if (!button) return;
        state.selectedYears = button.dataset.koreaForeignFlowRange === 'max' ? 'max' : Number(button.dataset.koreaForeignFlowRange);
        controls.querySelectorAll('[data-korea-foreign-flow-range]').forEach((item) => item.classList.toggle('is-active', item === button));
        render(container, state.rows, state.selectedYears);
      });
    }
  }

  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => {
    if (detail?.view === 'korea') chartUtils.scrollToLatest(document.querySelector('#korea-foreign-flow-chart .policy-expectation-chart-frame'));
  });
  window.MacroWatchDashboard?.registerLoader(load);
})();
