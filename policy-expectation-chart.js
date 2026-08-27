(() => {
  'use strict';

  const HEIGHT = 320;
  const MIN_VIEWPORT_WIDTH = 680;
  const Y_AXIS_WIDTH = 46;
  const PADDING = { top: 28, right: 24, bottom: 42, left: 12 };
  const DATABASE_PAGE_SIZE = 1000;
  const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000;
  const SCROLL_HISTORY_YEARS = 10;
  const state = { rows: [], selectedYears: 5 };

  const scale = (value, sourceMin, sourceMax, targetMin, targetMax) => sourceMax === sourceMin
    ? (targetMin + targetMax) / 2
    : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);

  function rowsForTimeline(rows, selectedYears) {
    if (selectedYears === 'max' || !rows.length) return rows;
    const latestDate = new Date(`${rows[rows.length - 1].observation_date}T00:00:00Z`);
    const cutoff = new Date(latestDate);
    cutoff.setUTCFullYear(cutoff.getUTCFullYear() - SCROLL_HISTORY_YEARS);
    return rows.filter((row) => Date.parse(`${row.observation_date}T00:00:00Z`) >= cutoff.getTime());
  }

  function scrollToLatest(frame) {
    if (!frame) return;
    window.requestAnimationFrame(() => { frame.scrollLeft = frame.scrollWidth - frame.clientWidth; });
  }

  function formatMonthDay(period) {
    const [, month, day] = String(period).split('-').map(Number);
    return `${month}월 ${day}일`;
  }

  function niceStep(value) {
    const magnitude = 10 ** Math.floor(Math.log10(Math.max(value, 1)));
    const normalized = value / magnitude;
    const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    return factor * magnitude;
  }

  function verticalScale(points) {
    const maximumObservedValue = Math.max(5, ...points.flatMap((point) => [
      Math.abs(point.value),
      Number.isFinite(point.fiveDayAverage) ? Math.abs(point.fiveDayAverage) : 0,
    ])) * 1.05;
    const tickStep = niceStep(maximumObservedValue / 2);
    return { tickStep, maximumAbsoluteValue: tickStep * 2 };
  }

  function withFiveDayAverage(rows) {
    return rows.map((row, index) => {
      if (index < 4) return { ...row, fiveDayAverage: null };
      const window = rows.slice(index - 4, index + 1).map((item) => Number(item.expectation_spread_bps));
      const fiveDayAverage = window.every(Number.isFinite)
        ? window.reduce((sum, value) => sum + value, 0) / window.length
        : null;
      return { ...row, fiveDayAverage };
    });
  }

  function render(container, rows, selectedYears) {
    const datedRows = rowsForTimeline(withFiveDayAverage(rows), selectedYears).map((row) => ({
      ...row,
      timestamp: Date.parse(`${row.observation_date}T00:00:00Z`),
      value: Number(row.expectation_spread_bps),
    })).filter((row) => Number.isFinite(row.timestamp) && Number.isFinite(row.value));
    if (!datedRows.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">정책금리 기대 스프레드 데이터가 아직 없습니다.</div>';
      return;
    }

    const firstTimestamp = datedRows[0].timestamp;
    const lastTimestamp = datedRows[datedRows.length - 1].timestamp;
    const viewportWidth = Math.max(MIN_VIEWPORT_WIDTH, (container.clientWidth || MIN_VIEWPORT_WIDTH) - Y_AXIS_WIDTH);
    const timelineWidth = selectedYears === 'max' || selectedYears === 10
      ? viewportWidth
      : Math.max(viewportWidth, viewportWidth * ((lastTimestamp - firstTimestamp) / (Number(selectedYears) * YEAR_MS)));
    const points = datedRows.map((row) => ({
      ...row,
      x: scale(row.timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right),
    }));
    const initialVerticalScale = verticalScale(points);
    const zeroY = scale(0, -initialVerticalScale.maximumAbsoluteValue, initialVerticalScale.maximumAbsoluteValue, HEIGHT - PADDING.bottom, PADDING.top);
    const pathFor = (sourcePoints, valueKey, maximumAbsoluteValue) => sourcePoints
      .filter((point) => Number.isFinite(point[valueKey]))
      .map((point, index) => {
        const y = scale(point[valueKey], -maximumAbsoluteValue, maximumAbsoluteValue, HEIGHT - PADDING.bottom, PADDING.top);
        return `${index ? 'L' : 'M'} ${point.x.toFixed(2)} ${y.toFixed(2)}`;
      }).join(' ');
    const rawPath = pathFor(points, 'value', initialVerticalScale.maximumAbsoluteValue);
    const averagePath = pathFor(points, 'fiveDayAverage', initialVerticalScale.maximumAbsoluteValue);
    const firstYear = new Date(firstTimestamp).getUTCFullYear();
    const lastYear = new Date(lastTimestamp).getUTCFullYear();
    const yearGuides = Array.from({ length: lastYear - firstYear + 1 }, (_, index) => {
      const year = firstYear + index;
      const timestamp = Date.UTC(year, 0, 1);
      if (timestamp < firstTimestamp || timestamp > lastTimestamp) return '';
      const x = scale(timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right);
      const yearLabel = selectedYears === 'max' ? String(year).slice(-2) : String(year);
      return `<line x1="${x}" y1="${PADDING.top}" x2="${x}" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-year-guide"/><text x="${x}" y="${HEIGHT - 10}" text-anchor="middle" class="policy-expectation-year">${yearLabel}</text>`;
    }).join('');
    const yTickValues = [-2, -1, 0, 1, 2].map((multiple) => {
      const value = multiple * initialVerticalScale.tickStep;
      const y = scale(value, -initialVerticalScale.maximumAbsoluteValue, initialVerticalScale.maximumAbsoluteValue, HEIGHT - PADDING.bottom, PADDING.top);
      const label = `${value > 0 ? '+' : ''}${Number(value.toFixed(2))}`;
      return { multiple, value, y, label };
    });
    const yGridLines = yTickValues.map(({ value, y }) => `<line x1="${PADDING.left}" y1="${y}" x2="${timelineWidth - PADDING.right}" y2="${y}" class="policy-expectation-y-grid${value === 0 ? ' policy-expectation-y-grid--zero' : ''}"/>`).join('');
    const yAxisLabels = yTickValues.map(({ multiple, y, label }) => `<line x1="${Y_AXIS_WIDTH - 5}" y1="${y}" x2="${Y_AXIS_WIDTH}" y2="${y}" class="policy-expectation-y-tick"/><text data-policy-expectation-y-multiple="${multiple}" x="${Y_AXIS_WIDTH - 9}" y="${y + 3}" text-anchor="end" class="policy-expectation-y-label">${label}</text>`).join('');
    const gradientSplit = ((zeroY - PADDING.top) / (HEIGHT - PADDING.top - PADDING.bottom) * 100).toFixed(2);

    container.innerHTML = `<div class="policy-expectation-chart-layout"><svg class="policy-expectation-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${yAxisLabels}</svg><div class="policy-expectation-chart-frame"><svg class="policy-expectation-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="0선을 중심으로 표시한 시장 내재 정책금리 기대 스프레드">
      <defs><linearGradient id="policy-expectation-line-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}"><stop offset="0%" stop-color="#b4535d"/><stop offset="${gradientSplit}%" stop-color="#b4535d"/><stop offset="${gradientSplit}%" stop-color="#2563a8"/><stop offset="100%" stop-color="#2563a8"/></linearGradient></defs>
      <g>${yearGuides}</g>
      <g>${yGridLines}</g>
      <text x="${PADDING.left + 4}" y="${zeroY - 7}" class="policy-expectation-zero-label">현재 정책 수준</text>
      <path d="${rawPath}" class="policy-expectation-line policy-expectation-line--raw"/>
      <path d="${averagePath}" class="policy-expectation-line policy-expectation-line--average"/>
      <line data-policy-expectation-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/>
      <text data-policy-expectation-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-expectation-cursor-detail"></text>
    </svg></div></div>`;

    const frame = container.querySelector('.policy-expectation-chart-frame');
    const svg = container.querySelector('.policy-expectation-chart-svg');
    const cursor = container.querySelector('[data-policy-expectation-cursor]');
    const cursorDetail = container.querySelector('[data-policy-expectation-detail]');
    const rawLine = container.querySelector('.policy-expectation-line--raw');
    const averageLine = container.querySelector('.policy-expectation-line--average');
    const yLabels = [...container.querySelectorAll('[data-policy-expectation-y-multiple]')];
    let scaleFrame = null;
    const updateVisibleScale = () => {
      scaleFrame = null;
      const visibleStart = frame.scrollLeft;
      const visibleEnd = visibleStart + frame.clientWidth;
      const visiblePoints = points.filter((point) => point.x >= visibleStart && point.x <= visibleEnd);
      if (!visiblePoints.length) return;
      const currentScale = verticalScale(visiblePoints);
      rawLine.setAttribute('d', pathFor(points, 'value', currentScale.maximumAbsoluteValue));
      averageLine.setAttribute('d', pathFor(points, 'fiveDayAverage', currentScale.maximumAbsoluteValue));
      yLabels.forEach((label) => {
        const value = Number(label.dataset.policyExpectationYMultiple) * currentScale.tickStep;
        label.textContent = `${value > 0 ? '+' : ''}${Number(value.toFixed(2))}`;
      });
    };
    frame.addEventListener('scroll', () => {
      if (scaleFrame !== null) return;
      scaleFrame = window.requestAnimationFrame(updateVisibleScale);
    }, { passive: true });
    scrollToLatest(frame);
    window.requestAnimationFrame(updateVisibleScale);
    frame.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = ((event.clientX - bounds.left) / bounds.width) * timelineWidth;
      const nearest = points.reduce((closest, point) => Math.abs(point.x - pointerX) < Math.abs(closest.x - pointerX) ? point : closest);
      cursor.setAttribute('x1', nearest.x);
      cursor.setAttribute('x2', nearest.x);
      cursorDetail.setAttribute('x', nearest.x);
      cursorDetail.textContent = formatMonthDay(nearest.observation_date);
      for (const element of [cursor, cursorDetail]) element.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => {
      for (const element of [cursor, cursorDetail]) element.classList.remove('is-visible');
    });
  }

  async function fetchAllRows(supabaseClient) {
    const rows = [];
    for (let from = 0; ; from += DATABASE_PAGE_SIZE) {
      const { data, error } = await supabaseClient.from('policy_expectation_spreads')
        .select('observation_date,near_term_spread_bps,cycle_spread_bps,expectation_spread_bps')
        .order('observation_date')
        .range(from, from + DATABASE_PAGE_SIZE - 1);
      if (error) throw error;
      const page = data || [];
      rows.push(...page);
      if (page.length < DATABASE_PAGE_SIZE) return rows;
    }
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('policy-expectation-chart');
    if (!container || !supabaseClient) return;
    try {
      state.rows = await fetchAllRows(supabaseClient);
      render(container, state.rows, state.selectedYears);
      const controls = document.querySelector('.policy-expectation-range-controls');
      if (controls && controls.dataset.bound !== 'true') {
        controls.dataset.bound = 'true';
        controls.addEventListener('click', (event) => {
          const button = event.target.closest('[data-policy-expectation-range]');
          if (!button) return;
          state.selectedYears = button.dataset.policyExpectationRange === 'max'
            ? 'max'
            : Number(button.dataset.policyExpectationRange);
          controls.querySelectorAll('[data-policy-expectation-range]').forEach((item) => item.classList.toggle('is-active', item === button));
          render(container, state.rows, state.selectedYears);
        });
      }
    } catch (error) {
      console.error('Policy expectation chart load failed:', error);
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">정책금리 기대 스프레드를 불러오지 못했습니다.</div>';
    }
  }

  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => {
    if (detail?.view !== 'policy') return;
    scrollToLatest(document.querySelector('#policy-expectation-chart .policy-expectation-chart-frame'));
  });

  window.MacroWatchDashboard?.registerLoader(load);
})();
