(() => {
  'use strict';

  const WIDTH = 900;
  const HEIGHT = 320;
  const PADDING = { top: 24, right: 22, bottom: 42, left: 22 };
  const CURSOR_PERIOD_WIDTH = 82;

  const scale = (value, sourceMin, sourceMax, targetMin, targetMax) => sourceMax === sourceMin
    ? (targetMin + targetMax) / 2
    : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);

  function render(container, rows) {
    if (!rows.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">FOMC 정책 점수 데이터가 아직 없습니다.</div>';
      return;
    }
    const values = rows.map((row) => Number(row.policy_index)).filter(Number.isFinite);
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const margin = Math.max(25, (maximum - minimum) * 0.12);
    const yMin = minimum - margin;
    const yMax = maximum + margin;
    const points = rows.map((row, index) => ({
      x: scale(index, 0, Math.max(1, rows.length - 1), PADDING.left, WIDTH - PADDING.right),
      y: scale(Number(row.policy_index), yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top),
      year: String(row.meeting_date).slice(0, 4),
      period: `${String(row.meeting_date).slice(2, 4)}년 ${String(row.meeting_date).slice(5, 7)}월`,
    }));
    const path = points.map((point, index) => `${index ? 'L' : 'M'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ');
    const years = [...new Map(points.map((point) => [point.year, point])).values()];
    container.innerHTML = `<div class="policy-chart-frame"><svg class="policy-chart-svg" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-label="FOMC 정책 스트레스 지수"><line x1="${PADDING.left}" y1="${HEIGHT - PADDING.bottom}" x2="${WIDTH - PADDING.right}" y2="${HEIGHT - PADDING.bottom}" class="policy-chart-axis"/><path d="${path}" class="policy-chart-line"/><g>${points.map((point) => `<circle cx="${point.x}" cy="${point.y}" r="3" class="policy-chart-point"/>`).join('')}</g><g>${years.map((point) => `<text x="${point.x}" y="${HEIGHT - 14}" text-anchor="middle" class="policy-chart-year">${point.year.slice(2)}</text>`).join('')}</g><line data-policy-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-chart-cursor"/><g data-policy-cursor-period class="policy-chart-cursor-period"><rect x="-${CURSOR_PERIOD_WIDTH / 2}" y="${HEIGHT - 34}" width="${CURSOR_PERIOD_WIDTH}" height="26" rx="5"/><text x="0" y="${HEIGHT - 16}" text-anchor="middle"></text></g></svg></div>`;
    const frame = container.querySelector('.policy-chart-frame');
    const cursor = container.querySelector('[data-policy-cursor]');
    const cursorPeriod = container.querySelector('[data-policy-cursor-period]');
    const cursorPeriodText = cursorPeriod.querySelector('text');
    frame.addEventListener('pointermove', (event) => {
      const bounds = frame.getBoundingClientRect();
      const x = Math.max(PADDING.left, Math.min(WIDTH - PADDING.right, ((event.clientX - bounds.left) / bounds.width) * WIDTH));
      const pointIndex = Math.round(scale(x, PADDING.left, WIDTH - PADDING.right, 0, points.length - 1));
      const labelX = Math.max(PADDING.left + CURSOR_PERIOD_WIDTH / 2, Math.min(WIDTH - PADDING.right - CURSOR_PERIOD_WIDTH / 2, x));
      cursor.setAttribute('x1', x);
      cursor.setAttribute('x2', x);
      cursorPeriod.setAttribute('transform', `translate(${labelX} 0)`);
      cursorPeriodText.textContent = points[pointIndex].period;
      cursor.classList.add('is-visible');
      cursorPeriod.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => {
      cursor.classList.remove('is-visible');
      cursorPeriod.classList.remove('is-visible');
    });
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('policy-signal-chart');
    if (!container || !supabaseClient) return;
    const { data, error } = await supabaseClient.from('central_bank_policy_events')
      .select('meeting_date,policy_index,final_event_score')
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
