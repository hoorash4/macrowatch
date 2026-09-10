(() => {
  'use strict';
  const chartUtils = window.MacroWatchAnalysisChart;
  const { lineWidths } = chartUtils;
  const supabaseClient = window.macroWatchSupabase || window.MacroWatchFrontend?.createSupabaseClient();
  const PROFILE = chartUtils.chartProfile({
    auxiliaryPanels: Object.freeze(['real-rates']),
    cursorSeries: Object.freeze([
      { key: 'headline_yoy_pct', label: '헤드라인', format: value => `${chartUtils.formatChartNumber(value)}%` },
      { key: 'core_yoy_pct', label: '코어', format: value => `${chartUtils.formatChartNumber(value)}%` },
    ]),
  });
  const state = { years: String(PROFILE.defaultYears), monthly: [], policy: [] };
  const COLORS = { headline: '#0f766e', core: '#ea580c', policy: '#334155', treasury: '#94a3b8' };
  const timestamp = value => Date.parse(`${String(value)}T00:00:00Z`);
  const finiteNumber = value => value === null || value === undefined || value === '' ? NaN : Number(value);
  const formatCursorMonth = value => String(value).slice(0, 7).replace('-', '.');
  const addMonthsIso = (value, months = 1) => {
    const parsed = new Date(timestamp(value));
    return new Date(Date.UTC(parsed.getUTCFullYear(), parsed.getUTCMonth() + months, 1)).toISOString().slice(0, 10);
  };
  function stepPath(rows, x, y, key) {
    const points = rows.filter(row => Number.isFinite(Number(row[key])));
    if (!points.length) return '';
    let path = `M ${x(points[0]).toFixed(2)} ${y(Number(points[0][key])).toFixed(2)}`;
    points.slice(1).forEach(row => { path += ` H ${x(row).toFixed(2)} V ${y(Number(row[key])).toFixed(2)}`; });
    return path;
  }

  function withTreasuryAverage(rows) {
    const observations = [];
    return rows.map(row => {
      const value = finiteNumber(row.treasury_10y_pct);
      if (!Number.isFinite(value)) return { ...row, treasury_10y_5d_pct: null };
      observations.push(value);
      const window = observations.slice(-5);
      return {
        ...row,
        treasury_10y_5d_pct: window.reduce((sum, point) => sum + point, 0) / window.length,
      };
    });
  }

  function renderLegend() {
    const legend = document.getElementById('inflation-model-legend');
    if (!legend) return;
    legend.innerHTML = [
      chartUtils.legendItem('헤드라인', { stroke: COLORS.headline, width: lineWidths.primary }),
      chartUtils.legendItem('헤드라인 잠정치', { stroke: COLORS.headline, width: lineWidths.primary, dash: '5 4' }),
      chartUtils.legendItem('코어', { stroke: COLORS.core, width: lineWidths.primary }),
      chartUtils.legendItem('코어 잠정치', { stroke: COLORS.core, width: lineWidths.primary, dash: '5 4' }),
      chartUtils.legendItem('기준금리', { stroke: COLORS.policy, width: lineWidths.auxiliary }),
      chartUtils.legendItem('미국 10년물 금리(5일 평균)', { stroke: COLORS.treasury, width: lineWidths.comparison }),
      chartUtils.legendItem('헤드라인 실질금리', { stroke: COLORS.headline, width: lineWidths.auxiliary }),
      chartUtils.legendItem('코어 실질금리', { stroke: COLORS.core, width: lineWidths.auxiliary }),
    ].join('');
  }

  function render() {
    const host = document.getElementById('inflation-model-chart');
    const realHost = document.getElementById('inflation-real-rate-chart');
    const monthly = state.monthly, policy = withTreasuryAverage(state.policy);
    if (!host || !realHost) return;
    if (!monthly.length || !policy.length) {
      host.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">저장된 통합물가 데이터가 없습니다.</div>';
      realHost.innerHTML = '';
      return;
    }

    const allDates = [...monthly.map(row => timestamp(addMonthsIso(row.month, 1))), ...policy.map(row => timestamp(row.observed_on))].filter(Number.isFinite);
    const first = Math.min(...allDates), last = Math.max(...allDates);
    const { baseWidth, mainHeight: height, auxiliaryHeight: realHeight } = chartUtils.chartLayout, padding = chartUtils.chartPadding('single', { top: chartUtils.chartLayout.plot.top, bottom: chartUtils.chartLayout.plot.bottom });
    const historyYears = (last - first) / (365.25 * 86400000);
    const width = state.years === 'max' ? baseWidth : Math.max(baseWidth, baseWidth * historyYears / Number(state.years));
    const xDate = value => padding.left + (timestamp(value) - first) / Math.max(1, last - first) * (width - padding.left - padding.right);
    const xInflation = row => xDate(addMonthsIso(row.month, 1));
    const xPolicy = row => xDate(row.observed_on);
    const values = [
      ...monthly.flatMap(row => [Number(row.headline_yoy_pct), Number(row.core_yoy_pct)]),
      ...policy.flatMap(row => [Number(row.target_upper_pct), finiteNumber(row.treasury_10y_5d_pct)]),
    ].filter(Number.isFinite);
    const domain = chartUtils.axisDomain(values, { includeZero: true, minimumSpan: 2 });
    const y = value => padding.top + (domain.max - value) / (domain.max - domain.min) * (height - padding.top - padding.bottom);
    const ticks = [...domain.ticks].reverse();
    const grid = ticks.map(value => `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(value)}" y2="${y(value)}" stroke="#dbe3ed" stroke-dasharray="3 4"/><text data-chart-left-axis x="${padding.left - 9}" y="${y(value) + 4}" text-anchor="end" fill="#64748b" font-size="10">${chartUtils.formatAxisNumber(value, { suffix: '%' })}</text>`).join('');
    const finalRows = monthly.filter(row => row.status === 'final');
    const provisionalRows = monthly.filter(row => row.status === 'provisional');
    const bridgeRows = provisionalRows.length && finalRows.length ? [finalRows.at(-1), ...provisionalRows] : provisionalRows;
    const path = (rows, key, yFor = y, xFor = xInflation) => chartUtils.monotoneSeriesPath(
      rows.filter(row => Number.isFinite(finiteNumber(row[key]))),
      row => xFor(row),
      row => yFor(finiteNumber(row[key])),
    );
    const displayRows = monthly.map(row => ({ ...row, display_month: addMonthsIso(row.month, 1) }));
    const years = [...new Set(displayRows.map(row => String(row.display_month).slice(0, 4)))];
    const guides = years.map(year => {
      const point = displayRows.find(row => String(row.display_month).startsWith(year));
      return point ? `<line x1="${xDate(point.display_month)}" x2="${xDate(point.display_month)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#edf0f4"/><text x="${xDate(point.display_month)}" y="${height - 16}" text-anchor="middle" class="analysis-chart-year-label">${year}</text>` : '';
    }).join('');
    host.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="헤드라인과 코어 통합물가지수, 기준금리와 미국 10년물 금리 추이">${grid}${guides}<path d="${path(finalRows, 'headline_yoy_pct')}" fill="none" stroke="${COLORS.headline}" stroke-width="${lineWidths.primary}" stroke-linecap="round"/><path d="${path(bridgeRows, 'headline_yoy_pct')}" fill="none" stroke="${COLORS.headline}" stroke-width="${lineWidths.primary}" stroke-dasharray="5 4" stroke-linecap="round"/><path d="${path(finalRows, 'core_yoy_pct')}" fill="none" stroke="${COLORS.core}" stroke-width="${lineWidths.primary}" stroke-linecap="round"/><path d="${path(bridgeRows, 'core_yoy_pct')}" fill="none" stroke="${COLORS.core}" stroke-width="${lineWidths.primary}" stroke-dasharray="5 4" stroke-linecap="round"/><path d="${stepPath(policy, xPolicy, y, 'target_upper_pct')}" fill="none" stroke="${COLORS.policy}" stroke-width="${lineWidths.auxiliary}"/><path d="${path(policy, 'treasury_10y_5d_pct', y, xPolicy)}" fill="none" stroke="${COLORS.treasury}" stroke-width="${lineWidths.comparison}" stroke-linecap="round"/><rect data-inflation-hit x="${padding.left}" y="${padding.top}" width="${width - padding.left - padding.right}" height="${height - padding.top - padding.bottom}" fill="transparent"/><line data-inflation-cursor x1="0" x2="0" y1="${padding.top}" y2="${height - padding.bottom}" class="policy-expectation-cursor"/><text data-inflation-value x="0" y="16" text-anchor="middle" class="analysis-chart-cursor-text analysis-chart-cursor-value" visibility="hidden"></text><text data-inflation-date x="0" y="${height - padding.bottom + 14}" text-anchor="middle" class="policy-expectation-cursor-detail analysis-chart-cursor-text analysis-chart-cursor-date"></text></svg>`;
    chartUtils.scrollableSvg(host.querySelector('svg'), width, baseWidth, {
      left: padding.left, right: padding.right,
      axisMode: padding.axisMode,
      top: padding.top, bottom: height - padding.bottom,
      axes: [{ side: 'left', y, points: [
        ...monthly.flatMap(row => [{ x: xInflation(row), value: Number(row.headline_yoy_pct) }, { x: xInflation(row), value: Number(row.core_yoy_pct) }]),
        ...policy.flatMap(row => [{ x: xPolicy(row), value: Number(row.target_upper_pct) }, { x: xPolicy(row), value: finiteNumber(row.treasury_10y_5d_pct) }]),
      ], selector: `path[stroke="${COLORS.headline}"],path[stroke="${COLORS.core}"],path[stroke="${COLORS.policy}"],path[stroke="${COLORS.treasury}"]`, format: value => chartUtils.formatAxisNumber(value, { suffix: '%' }) }],
    });

    const realPadding = chartUtils.chartPadding('single', { top: chartUtils.chartLayout.plot.top, bottom: chartUtils.chartLayout.plot.bottom });
    const realValues = monthly.flatMap(row => [Number(row.headline_real_rate_pct), Number(row.core_real_rate_pct)]).filter(Number.isFinite);
    const realDomain = chartUtils.axisDomain(realValues, { includeZero: true, minimumSpan: 2 });
    const realY = value => realPadding.top + (realDomain.max - value) / (realDomain.max - realDomain.min) * (realHeight - realPadding.top - realPadding.bottom);
    const realTicks = [...realDomain.ticks].reverse();
    const realGrid = realTicks.map(value => `<line x1="${realPadding.left}" x2="${width - realPadding.right}" y1="${realY(value)}" y2="${realY(value)}" stroke="#e2e8f0" stroke-dasharray="3 4"/><text data-chart-left-axis x="${realPadding.left - 9}" y="${realY(value) + 4}" text-anchor="end" fill="#64748b" font-size="10">${chartUtils.formatAxisNumber(value, { suffix: '%' })}</text>`).join('');
    const realZeroLine = `<line data-inflation-real-zero class="analysis-chart-zero-line" x1="${realPadding.left}" x2="${width - realPadding.right}" y1="${realY(0)}" y2="${realY(0)}"/>`;
    realHost.innerHTML = `<svg class="w-full" style="height:${realHeight}px" viewBox="0 0 ${width} ${realHeight}" role="img" aria-label="헤드라인과 코어 실질금리 보조지표">${realGrid}${realZeroLine}<path d="${path(monthly, 'headline_real_rate_pct', realY)}" fill="none" stroke="${COLORS.headline}" stroke-width="${lineWidths.auxiliary}" stroke-linecap="round"/><path d="${path(monthly, 'core_real_rate_pct', realY)}" fill="none" stroke="${COLORS.core}" stroke-width="${lineWidths.auxiliary}" stroke-linecap="round"/><line data-inflation-real-cursor x1="0" x2="0" y1="${realPadding.top}" y2="${realHeight - realPadding.bottom}" class="policy-expectation-cursor"/><text data-inflation-real-value x="0" y="14" text-anchor="middle" class="analysis-chart-cursor-text analysis-chart-cursor-value" visibility="hidden"></text><text data-inflation-real-date x="0" y="${realHeight - realPadding.bottom + 14}" text-anchor="middle" class="policy-expectation-cursor-detail analysis-chart-cursor-text analysis-chart-cursor-date"></text></svg>`;
    chartUtils.scrollableSvg(realHost.querySelector('svg'), width, baseWidth, {
      left: realPadding.left, right: realPadding.right,
      axisMode: realPadding.axisMode,
      xAxisMode: 'zero',
      showScrollbar: false,
      top: realPadding.top, bottom: realHeight - realPadding.bottom,
      axes: [{
        side: 'left',
        y: realY,
        points: monthly.flatMap(row => [{ x: xInflation(row), value: Number(row.headline_real_rate_pct) }, { x: xInflation(row), value: Number(row.core_real_rate_pct) }]),
        selector: `path[stroke="${COLORS.headline}"],path[stroke="${COLORS.core}"]`,
        referenceLines: [{ selector: '[data-inflation-real-zero]', value: 0 }],
        format: value => chartUtils.formatAxisNumber(value, { suffix: '%' }),
      }],
    });

    const mainFrame = host.querySelector('[data-history-scroll]'), realFrame = realHost.querySelector('[data-history-scroll]');
    const mainSvg = mainFrame?.querySelector('svg'), realSvg = realFrame?.querySelector('svg');
    const showMonth = nearest => {
      const cursorX = xInflation(nearest);
      const status = nearest.status === 'provisional' ? '잠정' : '확정';
      const mainCursor = host.querySelector('[data-inflation-cursor]'), realCursor = realHost.querySelector('[data-inflation-real-cursor]');
      [mainCursor, realCursor].forEach(cursor => { cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX); cursor.classList.add('is-visible'); });
      const mainValue = host.querySelector('[data-inflation-value]');
      mainValue.setAttribute('visibility', 'visible');
      mainValue.textContent = `${chartUtils.cursorValueText(nearest, PROFILE.cursorSeries)} (${status})`;
      const realValue = realHost.querySelector('[data-inflation-real-value]');
      realValue.setAttribute('visibility', 'hidden');
      const mainDate = host.querySelector('[data-inflation-date]'), realDate = realHost.querySelector('[data-inflation-real-date]');
      [mainDate, realDate].forEach(label => { label.textContent = formatCursorMonth(addMonthsIso(nearest.month, 1)); label.classList.add('is-visible'); });
      chartUtils.positionCursorText(mainValue, cursorX, mainFrame);
      chartUtils.positionCursorText(mainDate, cursorX, mainFrame);
      chartUtils.positionCursorText(realDate, cursorX, realFrame);
    };
    const clearMonth = () => {
      [host.querySelector('[data-inflation-cursor]'), realHost.querySelector('[data-inflation-real-cursor]')].forEach(node => node?.classList.remove('is-visible'));
      [host.querySelector('[data-inflation-date]'), realHost.querySelector('[data-inflation-real-date]')].forEach(node => node?.classList.remove('is-visible'));
      [host.querySelector('[data-inflation-value]'), realHost.querySelector('[data-inflation-real-value]')].forEach(node => node?.setAttribute('visibility', 'hidden'));
    };
    const attachPointer = (frame, svg) => frame?.addEventListener('pointermove', event => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = (event.clientX - bounds.left) / bounds.width * width;
      showMonth(monthly.reduce((closest, row) => Math.abs(xInflation(row) - pointerX) < Math.abs(xInflation(closest) - pointerX) ? row : closest));
    });
    attachPointer(mainFrame, mainSvg); attachPointer(realFrame, realSvg);
    mainFrame?.addEventListener('pointerleave', clearMonth); realFrame?.addEventListener('pointerleave', clearMonth);
    chartUtils.scrollToLatest(mainFrame); chartUtils.scrollToLatest(realFrame);
  }

  async function load() {
    const host = document.getElementById('inflation-model-chart');
    if (!host || !supabaseClient) return;
    const [monthlyResponse, policyResponse] = await Promise.all([
      chartUtils.loadAllRows((from, to) => supabaseClient.from('us_inflation_monthly').select('month,headline_yoy_pct,core_yoy_pct,policy_rate_upper_pct,headline_real_rate_pct,core_real_rate_pct,status,data_as_of').order('month', { ascending: true }).range(from, to)),
      chartUtils.loadAllRows((from, to) => supabaseClient.from('us_policy_rate_daily').select('observed_on,target_upper_pct,treasury_10y_pct').order('observed_on', { ascending: true }).range(from, to)),
    ]);
    if (monthlyResponse.error || policyResponse.error) {
      host.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">통합물가 데이터를 불러오지 못했습니다.</div>';
      return;
    }
    state.monthly = monthlyResponse.data || []; state.policy = policyResponse.data || []; render();
  }

  document.querySelectorAll('[data-inflation-ranges] [data-inflation-range]').forEach(button => button.addEventListener('click', () => {
    const value = button.dataset.inflationRange;
    if (!value || state.years === value) return;
    state.years = value;
    document.querySelectorAll('[data-inflation-range]').forEach(item => {
      const active = item.dataset.inflationRange === value;
      item.classList.toggle('is-active', active); item.setAttribute('aria-pressed', String(active));
    });
    render();
  }));

  renderLegend();
  window.MacroWatchDashboard?.registerLoader(load);
})();
