(() => {
  'use strict';

  const MIN_VIEWPORT_WIDTH = 680;
  const HEIGHT = 320;
  const Y_AXIS_WIDTH = 46;
  const PADDING = { top: 24, right: 22, bottom: 42, left: 12 };
  // 'legacy'로 바꾸면 DB의 기존 1000 누적 policy_index 표시로 즉시 원복됩니다.
  const POLICY_CHART_MODE = 'oscillator';
  const OSCILLATOR_RETENTION = 0.8;
  const STANDARD_MEETING_DAYS = 45;
  const state = { rows: [], selectedYears: 5 };
  const chartUtils = window.MacroWatchAnalysisChart;
  const scale = (value, sourceMin, sourceMax, targetMin, targetMax) => sourceMax === sourceMin
    ? (targetMin + targetMax) / 2
    : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);

  function withDisplayValues(rows) {
    if (POLICY_CHART_MODE === 'legacy') return rows.map((row) => ({ ...row, display_value: Number(row.policy_index) }));
    let previousValue = 0, previousDate = null;
    return rows.map((row) => {
      const currentDate = Date.parse(`${row.meeting_date}T00:00:00Z`);
      const elapsedDays = previousDate === null ? 0 : Math.max(0, (currentDate - previousDate) / (24 * 60 * 60 * 1000));
      const retainedValue = previousDate === null ? 0 : previousValue * (OSCILLATOR_RETENTION ** (elapsedDays / STANDARD_MEETING_DAYS));
      const displayValue = retainedValue + (Number(row.final_event_score) || 0);
      previousValue = displayValue; previousDate = currentDate;
      return { ...row, pre_event_value: retainedValue, display_value: displayValue };
    });
  }

  function visibleVerticalScale(points) {
    const values = points.flatMap((point) => [point.preValue, point.value]).filter(Number.isFinite);
    if (POLICY_CHART_MODE === 'oscillator') {
      const maximumAbsoluteValue = Math.max(1, ...values.map(Math.abs)) * 1.1;
      const tickStep = chartUtils.niceStep(maximumAbsoluteValue / 2);
      return { tickStep, yMin: -tickStep * 2, yMax: tickStep * 2 };
    }
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const span = Math.max(maximum - minimum, 1);
    const margin = span * 0.1;
    const tickStep = chartUtils.niceStep((span + margin * 2) / 4);
    let yMin = Math.floor((minimum - margin) / tickStep) * tickStep;
    let yMax = yMin + tickStep * 4;
    // 눈금 반올림이 어느 한쪽으로 치우쳐도 실제 값과 여백이 축 밖으로 나가지 않게 맞춥니다.
    if (yMax < maximum + margin) {
      yMax = Math.ceil((maximum + margin) / tickStep) * tickStep;
      yMin = yMax - tickStep * 4;
    }
    return { tickStep, yMin, yMax };
  }

  function render(container, rows, selectedYears) {
    const datedRows = chartUtils.rowsForRecentHistory(rows, 'meeting_date', selectedYears).map((row) => ({
      ...row, timestamp: Date.parse(`${row.meeting_date}T00:00:00Z`), value: Number(row.display_value),
    })).filter((row) => Number.isFinite(row.timestamp) && Number.isFinite(row.value));
    if (!datedRows.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">FOMC 정책 점수 데이터가 아직 없습니다.</div>';
      return;
    }
    const firstTimestamp = datedRows[0].timestamp;
    const lastTimestamp = datedRows[datedRows.length - 1].timestamp;
    const viewportWidth = Math.max(MIN_VIEWPORT_WIDTH, (container.clientWidth || MIN_VIEWPORT_WIDTH) - Y_AXIS_WIDTH);
    const timelineWidth = chartUtils.timelineWidth(viewportWidth, firstTimestamp, lastTimestamp, selectedYears);
    const points = datedRows.map((row) => ({
      x: scale(row.timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right), value: row.value,
      preValue: Number(row.pre_event_value),
      period: `${String(row.meeting_date).slice(2, 4)}년 ${String(row.meeting_date).slice(5, 7)}월`,
      meetingDate: row.meeting_date,
      action: ({ hike: '인상', cut: '인하', hold: '동결' })[row.action] || row.action,
      changeBps: row.change_bps == null ? null : Math.abs(Number(row.change_bps)),
      eventScore: Number(row.final_event_score) || 0,
    }));
    const initialScale = visibleVerticalScale(points);
    // 회의 사이 감쇠와 회의 당일 점수를 분리해 점수의 부호가 선 방향에 드러나게 합니다.
    const pathFor = ({ yMin, yMax }) => points.map((point, index) => {
      const x = point.x.toFixed(2);
      const preY = scale(point.preValue, yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top).toFixed(2);
      const postY = scale(point.value, yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top).toFixed(2);
      return `${index ? 'L' : 'M'} ${x} ${preY} L ${x} ${postY}`;
    }).join(' ');
    const circlesFor = ({ yMin, yMax }) => points.map((point) => `<circle cx="${point.x}" cy="${scale(point.value, yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top)}" r="3" class="policy-chart-point"/>`).join('');
    const firstYear = new Date(firstTimestamp).getUTCFullYear();
    const lastYear = new Date(lastTimestamp).getUTCFullYear();
    const yearTicks = Array.from({ length: lastYear - firstYear + 1 }, (_, index) => {
      const year = firstYear + index;
      const timestamp = Date.UTC(year, 0, 1);
      if (timestamp < firstTimestamp || timestamp > lastTimestamp) return '';
      const x = scale(timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right);
      const label = selectedYears === 'max' ? String(year).slice(-2) : String(year);
      return `<line x1="${x}" y1="${PADDING.top}" x2="${x}" y2="${HEIGHT - PADDING.bottom}" class="policy-chart-year-guide"/><text x="${x}" y="${HEIGHT - 10}" text-anchor="middle" class="policy-chart-year">${label}</text>`;
    }).join('');
    const tickMultiples = [0, 1, 2, 3, 4];
    const gridLines = tickMultiples.map((multiple) => {
      const value = initialScale.yMin + initialScale.tickStep * multiple;
      return `<line data-policy-y-grid="${multiple}" x1="${PADDING.left}" y1="${scale(multiple, 0, 4, HEIGHT - PADDING.bottom, PADDING.top)}" x2="${timelineWidth - PADDING.right}" y2="${scale(multiple, 0, 4, HEIGHT - PADDING.bottom, PADDING.top)}" class="policy-chart-y-grid${Math.abs(value) < 1e-9 ? ' policy-chart-y-grid--zero' : ''}"/>`;
    }).join('');
    const axisLabels = tickMultiples.map((multiple) => {
      const y = scale(multiple, 0, 4, HEIGHT - PADDING.bottom, PADDING.top);
      const value = initialScale.yMin + initialScale.tickStep * multiple;
      return `<line x1="${Y_AXIS_WIDTH - 5}" y1="${y}" x2="${Y_AXIS_WIDTH}" y2="${y}" class="policy-chart-y-tick"/><text data-policy-y-multiple="${multiple}" x="${Y_AXIS_WIDTH - 9}" y="${y + 3}" text-anchor="end" class="policy-chart-y-label">${Number(value.toFixed(2))}</text>`;
    }).join('');
    container.innerHTML = `<div class="policy-chart-layout"><svg class="policy-chart-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${axisLabels}</svg><div class="policy-chart-frame"><svg class="policy-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="FOMC 정책 스트레스 지수"><g>${yearTicks}</g><g>${gridLines}</g><path d="${pathFor(initialScale)}" class="policy-chart-line"/><g data-policy-points>${circlesFor(initialScale)}</g><line data-policy-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-chart-cursor"/><text data-policy-cursor-action text-anchor="middle" y="${PADDING.top + 11}" class="policy-chart-cursor-action"></text><text data-policy-cursor-period text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-chart-cursor-period"></text></svg></div></div>`;
    const frame = container.querySelector('.policy-chart-frame');
    const svg = container.querySelector('.policy-chart-svg');
    const line = container.querySelector('.policy-chart-line');
    const pointGroup = container.querySelector('[data-policy-points]');
    const yLabels = [...container.querySelectorAll('[data-policy-y-multiple]')];
    const yGridLines = [...container.querySelectorAll('[data-policy-y-grid]')];
    const cursor = container.querySelector('[data-policy-cursor]');
    const cursorPeriod = container.querySelector('[data-policy-cursor-period]');
    const cursorAction = container.querySelector('[data-policy-cursor-action]');
    const adminLink = document.getElementById('admin-page-link');
    let selectedPoint = null;
    let scaleFrame = null;
    const updateVisibleScale = () => {
      scaleFrame = null;
      const visibleStart = frame.scrollLeft;
      const visibleEnd = visibleStart + frame.clientWidth;
      const visiblePoints = points.filter((point) => point.x >= visibleStart && point.x <= visibleEnd);
      if (!visiblePoints.length) return;
      const currentScale = visibleVerticalScale(visiblePoints);
      line.setAttribute('d', pathFor(currentScale));
      pointGroup.innerHTML = circlesFor(currentScale);
      yLabels.forEach((label) => {
        const value = currentScale.yMin + Number(label.dataset.policyYMultiple) * currentScale.tickStep;
        label.textContent = `${value > 0 && POLICY_CHART_MODE === 'oscillator' ? '+' : ''}${Number(value.toFixed(2))}`;
      });
      yGridLines.forEach((gridLine) => {
        const value = currentScale.yMin + Number(gridLine.dataset.policyYGrid) * currentScale.tickStep;
        gridLine.classList.toggle('policy-chart-y-grid--zero', Math.abs(value) < 1e-9);
      });
    };
    frame.addEventListener('scroll', () => {
      if (scaleFrame === null) scaleFrame = window.requestAnimationFrame(updateVisibleScale);
    }, { passive: true });
    chartUtils.scrollToLatest(frame);
    window.requestAnimationFrame(updateVisibleScale);
    frame.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = ((event.clientX - bounds.left) / bounds.width) * timelineWidth;
      const nearest = points.reduce((closest, point) => Math.abs(point.x - pointerX) < Math.abs(closest.x - pointerX) ? point : closest);
      selectedPoint = nearest;
      cursor.setAttribute('x1', nearest.x); cursor.setAttribute('x2', nearest.x);
      cursorPeriod.setAttribute('x', nearest.x); cursorPeriod.textContent = nearest.period;
      cursorAction.setAttribute('x', nearest.x); cursorAction.textContent = `${nearest.action}${Number.isFinite(nearest.changeBps) ? `(${nearest.changeBps}bp)` : ''} · 점수 ${nearest.eventScore > 0 ? '+' : ''}${Math.round(nearest.eventScore)}`;
      for (const element of [cursor, cursorPeriod, cursorAction]) element.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => {
      selectedPoint = null;
      for (const element of [cursor, cursorPeriod, cursorAction]) element.classList.remove('is-visible');
    });
    svg.addEventListener('click', () => {
      if (!selectedPoint || !adminLink || adminLink.hidden) return;
      const storageKey = 'macrowatch_policy_review_dates';
      let dates = [];
      try { dates = JSON.parse(window.localStorage.getItem(storageKey) || '[]'); } catch (_) { dates = []; }
      dates = [...new Set([...(Array.isArray(dates) ? dates : []), selectedPoint.meetingDate])];
      window.localStorage.setItem(storageKey, JSON.stringify(dates));
      window.MacroWatchDashboard?.showNotice('FOMC 수정 목록 등록', `${selectedPoint.meetingDate} 회의를 관리자 수정 목록에 추가했습니다.`);
    });
  }

  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => {
    if (detail?.view !== 'policy') return;
    chartUtils.scrollToLatest(document.querySelector('#policy-signal-chart .policy-chart-frame'));
  });

  async function load({ supabaseClient }) {
    const container = document.getElementById('policy-signal-chart');
    if (!container || !supabaseClient) return;
    const { data, error } = await supabaseClient.from('central_bank_policy_events').select('meeting_date,action,change_bps,policy_index,final_event_score').eq('central_bank', 'fed').eq('analysis_status', 'completed').not('policy_index', 'is', null).order('meeting_date');
    if (error) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">정책 점수를 불러오지 못했습니다.</div>';
      return;
    }
    state.rows = withDisplayValues(data || []);
    render(container, state.rows, state.selectedYears);
    const controls = document.querySelector('[data-policy-chart-ranges]');
    if (controls && controls.dataset.bound !== 'true') {
      controls.dataset.bound = 'true';
      controls.addEventListener('click', (event) => {
        const button = event.target.closest('[data-policy-chart-range]');
        if (!button) return;
        state.selectedYears = button.dataset.policyChartRange === 'max' ? 'max' : Number(button.dataset.policyChartRange);
        controls.querySelectorAll('[data-policy-chart-range]').forEach((item) => item.classList.toggle('is-active', item === button));
        render(container, state.rows, state.selectedYears);
      });
    }
  }

  window.MacroWatchDashboard?.registerLoader(load);
})();
