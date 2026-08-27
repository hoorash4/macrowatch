(() => {
  'use strict';

  const HEIGHT = 320;
  const MIN_VIEWPORT_WIDTH = 680;
  const PADDING = { top: 28, right: 24, bottom: 42, left: 24 };
  const DATABASE_PAGE_SIZE = 1000;
  const state = { rows: [], selectedYears: 5 };

  const scale = (value, sourceMin, sourceMax, targetMin, targetMax) => sourceMax === sourceMin
    ? (targetMin + targetMax) / 2
    : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);

  function rowsForSelectedRange(rows, selectedYears) {
    if (selectedYears === 'max' || !rows.length) return rows;
    const latestDate = new Date(`${rows[rows.length - 1].observation_date}T00:00:00Z`);
    const cutoff = new Date(latestDate);
    cutoff.setUTCFullYear(cutoff.getUTCFullYear() - Number(selectedYears));
    return rows.filter((row) => Date.parse(`${row.observation_date}T00:00:00Z`) >= cutoff.getTime());
  }

  function formatDate(period) {
    const [year, month, day] = String(period).split('-').map(Number);
    return `${year}년 ${month}월 ${day}일`;
  }

  function formatBps(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '—';
    return `${numeric > 0 ? '+' : ''}${numeric.toFixed(1)}bp`;
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
    const datedRows = rowsForSelectedRange(withFiveDayAverage(rows), selectedYears).map((row) => ({
      ...row,
      timestamp: Date.parse(`${row.observation_date}T00:00:00Z`),
      value: Number(row.expectation_spread_bps),
    })).filter((row) => Number.isFinite(row.timestamp) && Number.isFinite(row.value));
    if (!datedRows.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">정책금리 기대 스프레드 데이터가 아직 없습니다.</div>';
      return;
    }

    const maximumAbsoluteValue = Math.max(25, ...datedRows.map((row) => Math.abs(row.value))) * 1.1;
    const firstTimestamp = datedRows[0].timestamp;
    const lastTimestamp = datedRows[datedRows.length - 1].timestamp;
    const viewportWidth = Math.max(MIN_VIEWPORT_WIDTH, container.clientWidth || MIN_VIEWPORT_WIDTH);
    const timelineWidth = viewportWidth;
    const zeroY = scale(0, -maximumAbsoluteValue, maximumAbsoluteValue, HEIGHT - PADDING.bottom, PADDING.top);
    const points = datedRows.map((row) => ({
      ...row,
      x: scale(row.timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right),
      y: scale(row.value, -maximumAbsoluteValue, maximumAbsoluteValue, HEIGHT - PADDING.bottom, PADDING.top),
    }));
    const rawPath = points.map((point, index) => `${index ? 'L' : 'M'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ');
    const averagePoints = points.filter((point) => Number.isFinite(point.fiveDayAverage)).map((point) => ({
      ...point,
      averageY: scale(point.fiveDayAverage, -maximumAbsoluteValue, maximumAbsoluteValue, HEIGHT - PADDING.bottom, PADDING.top),
    }));
    const averagePath = averagePoints.map((point, index) => `${index ? 'L' : 'M'} ${point.x.toFixed(2)} ${point.averageY.toFixed(2)}`).join(' ');
    const firstYear = new Date(firstTimestamp).getUTCFullYear();
    const lastYear = new Date(lastTimestamp).getUTCFullYear();
    const yearGuides = Array.from({ length: lastYear - firstYear + 1 }, (_, index) => {
      const year = firstYear + index;
      const timestamp = Date.UTC(year, 0, 1);
      if (timestamp < firstTimestamp || timestamp > lastTimestamp) return '';
      const x = scale(timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right);
      const yearLabel = selectedYears === 'max' ? String(year).slice(-2) : String(year);
      return `<line x1="${x}" y1="${PADDING.top}" x2="${x}" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-year-guide"/><line x1="${x}" y1="${HEIGHT - PADDING.bottom}" x2="${x}" y2="${HEIGHT - PADDING.bottom + 5}" class="policy-expectation-x-tick"/><text x="${x}" y="${HEIGHT - 10}" text-anchor="middle" class="policy-expectation-year">${yearLabel}</text>`;
    }).join('');
    const gradientSplit = ((zeroY - PADDING.top) / (HEIGHT - PADDING.top - PADDING.bottom) * 100).toFixed(2);

    container.innerHTML = `<div class="policy-expectation-chart-frame"><svg class="policy-expectation-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="0선을 중심으로 표시한 시장 내재 정책금리 기대 스프레드">
      <defs><linearGradient id="policy-expectation-line-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}"><stop offset="0%" stop-color="#b4535d"/><stop offset="${gradientSplit}%" stop-color="#b4535d"/><stop offset="${gradientSplit}%" stop-color="#2563a8"/><stop offset="100%" stop-color="#2563a8"/></linearGradient></defs>
      <g>${yearGuides}</g>
      <line x1="${PADDING.left}" y1="${HEIGHT - PADDING.bottom}" x2="${timelineWidth - PADDING.right}" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-x-axis"/>
      <line x1="${PADDING.left}" y1="${zeroY}" x2="${timelineWidth - PADDING.right}" y2="${zeroY}" class="policy-expectation-zero-line"/>
      <text x="${PADDING.left + 4}" y="${zeroY - 7}" class="policy-expectation-zero-label">0 · 현재 정책 수준</text>
      <path d="${rawPath}" class="policy-expectation-line policy-expectation-line--raw"/>
      <path d="${averagePath}" class="policy-expectation-line policy-expectation-line--average"/>
      <line data-policy-expectation-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/>
      <text data-policy-expectation-value text-anchor="middle" y="${PADDING.top + 11}" class="policy-expectation-cursor-value"></text>
      <text data-policy-expectation-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-expectation-cursor-detail"></text>
    </svg></div>`;

    const frame = container.querySelector('.policy-expectation-chart-frame');
    const svg = container.querySelector('.policy-expectation-chart-svg');
    const cursor = container.querySelector('[data-policy-expectation-cursor]');
    const cursorValue = container.querySelector('[data-policy-expectation-value]');
    const cursorDetail = container.querySelector('[data-policy-expectation-detail]');
    frame.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = ((event.clientX - bounds.left) / bounds.width) * timelineWidth;
      const nearest = points.reduce((closest, point) => Math.abs(point.x - pointerX) < Math.abs(closest.x - pointerX) ? point : closest);
      cursor.setAttribute('x1', nearest.x);
      cursor.setAttribute('x2', nearest.x);
      cursorValue.setAttribute('x', nearest.x);
      cursorValue.textContent = `${formatDate(nearest.observation_date)} · 5일 평균 ${formatBps(nearest.fiveDayAverage)}`;
      cursorDetail.setAttribute('x', nearest.x);
      cursorDetail.textContent = `일간 ${formatBps(nearest.value)} · 3개월 ${formatBps(nearest.near_term_spread_bps)} · 2년 ${formatBps(nearest.cycle_spread_bps)}`;
      for (const element of [cursor, cursorValue, cursorDetail]) element.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => {
      for (const element of [cursor, cursorValue, cursorDetail]) element.classList.remove('is-visible');
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

  window.MacroWatchDashboard?.registerLoader(load);
})();
