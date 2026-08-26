(() => {
  'use strict';

  const MIN_VIEWPORT_WIDTH = 680;
  const HEIGHT = 320;
  const PADDING = { top: 24, right: 22, bottom: 42, left: 22 };
  const TEN_YEARS_MS = 10 * 365.25 * 24 * 60 * 60 * 1000;

  function scrollToLatest(frame) {
    if (!frame) return;
    // 메뉴가 실제로 표시된 다음 레이아웃 폭이 확정된 시점에 최신 회의로 이동합니다.
    window.requestAnimationFrame(() => { frame.scrollLeft = frame.scrollWidth - frame.clientWidth; });
  }

  const scale = (value, sourceMin, sourceMax, targetMin, targetMax) => sourceMax === sourceMin
    ? (targetMin + targetMax) / 2
    : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);

  function render(container, rows) {
    if (!rows.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">FOMC 정책 점수 데이터가 아직 없습니다.</div>';
      return;
    }
    const datedRows = rows.map((row) => ({ ...row, timestamp: Date.parse(`${row.meeting_date}T00:00:00Z`) }))
      .filter((row) => Number.isFinite(row.timestamp));
    const values = datedRows.map((row) => Number(row.policy_index)).filter(Number.isFinite);
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const margin = Math.max(25, (maximum - minimum) * 0.12);
    const yMin = minimum - margin;
    const yMax = maximum + margin;
    const firstTimestamp = datedRows[0].timestamp;
    const lastTimestamp = datedRows[datedRows.length - 1].timestamp;
    const viewportWidth = Math.max(MIN_VIEWPORT_WIDTH, container.clientWidth || MIN_VIEWPORT_WIDTH);
    const timelineWidth = Math.max(viewportWidth, viewportWidth * ((lastTimestamp - firstTimestamp) / TEN_YEARS_MS));
    const points = datedRows.map((row) => ({
      x: scale(row.timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right),
      y: scale(Number(row.policy_index), yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top),
      year: String(row.meeting_date).slice(0, 4),
      period: `${String(row.meeting_date).slice(2, 4)}년 ${String(row.meeting_date).slice(5, 7)}월`,
      action: ({ hike: '인상', cut: '인하', hold: '동결' })[row.action] || row.action,
      changeBps: row.change_bps == null ? null : Math.abs(Number(row.change_bps)),
    }));
    const path = points.map((point, index) => `${index ? 'L' : 'M'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ');
    const firstYear = new Date(firstTimestamp).getUTCFullYear();
    const lastYear = new Date(lastTimestamp).getUTCFullYear();
    const yearTicks = Array.from({ length: lastYear - firstYear + 1 }, (_, index) => {
      const year = firstYear + index;
      const timestamp = Date.UTC(year, 0, 1);
      if (timestamp < firstTimestamp || timestamp > lastTimestamp) return '';
      const x = scale(timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right);
      return `<line x1="${x}" y1="${PADDING.top}" x2="${x}" y2="${HEIGHT - PADDING.bottom}" class="policy-chart-year-guide"/><text x="${x}" y="${HEIGHT - 10}" text-anchor="middle" class="policy-chart-year">${year}</text>`;
    }).join('');
    container.innerHTML = `<div class="policy-chart-frame"><svg class="policy-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="FOMC 정책 스트레스 지수"><line x1="${PADDING.left}" y1="${HEIGHT - PADDING.bottom}" x2="${timelineWidth - PADDING.right}" y2="${HEIGHT - PADDING.bottom}" class="policy-chart-axis"/><g>${yearTicks}</g><path d="${path}" class="policy-chart-line"/><g>${points.map((point) => `<circle cx="${point.x}" cy="${point.y}" r="3" class="policy-chart-point"/>`).join('')}</g><line data-policy-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-chart-cursor"/><text data-policy-cursor-action text-anchor="middle" y="${PADDING.top + 11}" class="policy-chart-cursor-action"></text><text data-policy-cursor-period text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-chart-cursor-period"></text></svg></div>`;
    const frame = container.querySelector('.policy-chart-frame');
    const svg = container.querySelector('.policy-chart-svg');
    const cursor = container.querySelector('[data-policy-cursor]');
    const cursorPeriod = container.querySelector('[data-policy-cursor-period]');
    const cursorAction = container.querySelector('[data-policy-cursor-action]');
    scrollToLatest(frame);
    frame.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = ((event.clientX - bounds.left) / bounds.width) * timelineWidth;
      const nearest = points.reduce((closest, point) => Math.abs(point.x - pointerX) < Math.abs(closest.x - pointerX) ? point : closest);
      cursor.setAttribute('x1', nearest.x);
      cursor.setAttribute('x2', nearest.x);
      cursorPeriod.setAttribute('x', nearest.x);
      cursorPeriod.textContent = nearest.period;
      cursorAction.setAttribute('x', nearest.x);
      cursorAction.textContent = `${nearest.action}${Number.isFinite(nearest.changeBps) ? `(${nearest.changeBps}bp)` : ''}`;
      cursor.classList.add('is-visible');
      cursorPeriod.classList.add('is-visible');
      cursorAction.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => {
      cursor.classList.remove('is-visible');
      cursorPeriod.classList.remove('is-visible');
      cursorAction.classList.remove('is-visible');
    });
  }

  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => {
    if (detail?.view !== 'policy') return;
    scrollToLatest(document.querySelector('#policy-signal-chart .policy-chart-frame'));
  });

  async function load({ supabaseClient }) {
    const container = document.getElementById('policy-signal-chart');
    if (!container || !supabaseClient) return;
    const { data, error } = await supabaseClient.from('central_bank_policy_events')
      .select('meeting_date,action,change_bps,policy_index,final_event_score')
      .eq('central_bank', 'fed').eq('analysis_status', 'completed')
      .not('policy_index', 'is', null).order('meeting_date');
    if (error) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">정책 점수를 불러오지 못했습니다.</div>';
      return;
    }
    render(container, data || []);
  }

  window.MacroWatchDashboard?.registerLoader(load);
})();
