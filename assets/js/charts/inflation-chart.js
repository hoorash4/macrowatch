(() => {
  'use strict';

  const chartUtils = window.MacroWatchAnalysisChart;
  const supabaseClient = window.macroWatchSupabase || window.MacroWatchFrontend?.createSupabaseClient();
  const state = { kind: 'headline', years: '2', monthly: [], daily: [] };
  const COLORS = {
    actual: '#0f766e',
    provisional: '#d97706',
    leading: '#ea580c',
    policy: '#334155',
    real: '#7c3aed',
  };

  function timestamp(value) {
    return Date.parse(`${String(value)}T00:00:00Z`);
  }

  function stepPath(rows, x, y, key) {
    const points = rows.filter(row => Number.isFinite(Number(row[key])));
    if (!points.length) return '';
    let path = `M ${x(points[0]).toFixed(2)} ${y(Number(points[0][key])).toFixed(2)}`;
    points.slice(1).forEach((row, index) => {
      path += ` H ${x(row).toFixed(2)} V ${y(Number(row[key])).toFixed(2)}`;
    });
    return path;
  }

  function renderLegend() {
    const legend = document.getElementById('inflation-model-legend');
    if (!legend) return;
    legend.innerHTML = [
      chartUtils.legendItem('통합물가', { stroke: COLORS.actual, width: 3 }),
      chartUtils.legendItem('잠정치', { stroke: COLORS.provisional, width: 3, dash: '5 4' }),
      chartUtils.legendItem('시장 선행', { stroke: COLORS.leading, width: 2.5 }),
      chartUtils.legendItem('기준금리', { stroke: COLORS.policy, width: 2.25 }),
      chartUtils.legendItem('실질금리', { stroke: COLORS.real, width: 2.25 }),
    ].join('');
  }

  function render() {
    const host = document.getElementById('inflation-model-chart');
    if (!host) return;
    const monthly = state.monthly;
    const daily = state.daily;
    if (!monthly.length || !daily.length) {
      host.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">저장된 통합물가 데이터가 없습니다.</div>';
      return;
    }

    const actualKey = `${state.kind}_yoy_pct`;
    const leadKey = `${state.kind}_leading_yoy_pct`;
    const realMonthlyKey = `${state.kind}_real_rate_pct`;
    const allDates = [
      ...monthly.map(row => timestamp(row.month)),
      ...daily.map(row => timestamp(row.observed_on)),
    ].filter(Number.isFinite);
    const first = Math.min(...allDates), last = Math.max(...allDates);
    const baseWidth = 920, height = 360, padding = { left: 58, right: 24, top: 24, bottom: 42 };
    const historyYears = (last - first) / (365.25 * 86400000);
    const width = state.years === 'max' ? baseWidth : Math.max(baseWidth, baseWidth * historyYears / Number(state.years));
    const x = row => {
      const value = timestamp(row.observed_on || row.month);
      return padding.left + (value - first) / Math.max(1, last - first) * (width - padding.left - padding.right);
    };
    const values = [
      ...monthly.flatMap(row => [Number(row[actualKey]), Number(row.policy_rate_upper_pct), Number(row[realMonthlyKey])]),
      ...daily.map(row => Number(row[leadKey])),
    ].filter(Number.isFinite);
    const domain = chartUtils.axisDomain(values, { includeZero: true, minimumSpan: 2 });
    const y = value => padding.top + (domain.max - value) / (domain.max - domain.min) * (height - padding.top - padding.bottom);
    const ticks = Array.from({ length: 6 }, (_, index) => domain.max - (domain.max - domain.min) * index / 5);
    const grid = ticks.map(value => `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(value)}" y2="${y(value)}" stroke="#dbe3ed"${Math.abs(value) < .01 ? ' stroke-width="1.5"' : ' stroke-dasharray="3 4"'}/><text x="${padding.left - 9}" y="${y(value) + 4}" text-anchor="end" fill="#64748b" font-size="10">${value.toFixed(1)}%</text>`).join('');

    const actualRows = monthly.filter(row => row.status === 'final');
    const provisionalRows = monthly.filter(row => row.status === 'provisional');
    const actualPath = chartUtils.monotoneSeriesPath(actualRows, row => x(row), row => y(Number(row[actualKey])));
    const bridgeRows = provisionalRows.length && actualRows.length ? [actualRows.at(-1), ...provisionalRows] : provisionalRows;
    const provisionalPath = chartUtils.monotoneSeriesPath(bridgeRows, row => x(row), row => y(Number(row[actualKey])));
    const leadPath = chartUtils.monotoneSeriesPath(daily, row => x(row), row => y(Number(row[leadKey])));
    const policyRows = daily.map(row => ({ ...row, policy_rate_upper_pct: Number(row.policy_rate_upper_pct) }));
    const policyPath = stepPath(policyRows, x, y, 'policy_rate_upper_pct');
    const realPath = chartUtils.monotoneSeriesPath(monthly, row => x(row), row => y(Number(row[realMonthlyKey])));
    const years = [...new Set(monthly.map(row => String(row.month).slice(0, 4)))];
    const guides = years.map(year => {
      const point = monthly.find(row => String(row.month).startsWith(year));
      if (!point) return '';
      return `<line x1="${x(point)}" x2="${x(point)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#edf0f4"/><text x="${x(point) + 4}" y="${height - 14}" fill="#64748b" font-size="10">${year}</text>`;
    }).join('');
    host.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="통합물가, 시장 선행지표, 기준금리와 실질금리 추이">${grid}${guides}<path d="${actualPath}" fill="none" stroke="${COLORS.actual}" stroke-width="3" stroke-linecap="round"/><path d="${provisionalPath}" fill="none" stroke="${COLORS.provisional}" stroke-width="3" stroke-dasharray="5 4" stroke-linecap="round"/><path d="${leadPath}" fill="none" stroke="${COLORS.leading}" stroke-width="2.5" stroke-linecap="round"/><path d="${policyPath}" fill="none" stroke="${COLORS.policy}" stroke-width="2.25"/><path d="${realPath}" fill="none" stroke="${COLORS.real}" stroke-width="2.25" stroke-linecap="round"/><rect data-inflation-hit x="${padding.left}" y="${padding.top}" width="${width - padding.left - padding.right}" height="${height - padding.top - padding.bottom}" fill="transparent"/><line data-inflation-cursor x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" class="policy-expectation-cursor"/><text data-inflation-value x="0" y="16" text-anchor="middle" fill="#334155" font-size="11" font-weight="700" visibility="hidden"></text><text data-inflation-date x="0" y="${height - padding.bottom + 14}" text-anchor="middle" class="policy-expectation-cursor-detail"></text></svg>`;
    chartUtils.scrollableSvg(host.querySelector('svg'), width, baseWidth, {
      top: padding.top,
      bottom: height - padding.bottom,
      axes: [{
        side: 'left', y,
        points: [
          ...monthly.flatMap(row => [
            { x: x(row), value: Number(row[actualKey]) },
            { x: x(row), value: Number(row[realMonthlyKey]) },
          ]),
          ...daily.flatMap(row => [
            { x: x(row), value: Number(row[leadKey]) },
            { x: x(row), value: Number(row.policy_rate_upper_pct) },
          ]),
        ],
        selector: `path[stroke="${COLORS.actual}"],path[stroke="${COLORS.provisional}"],path[stroke="${COLORS.leading}"],path[stroke="${COLORS.policy}"],path[stroke="${COLORS.real}"]`,
        format: value => `${value.toFixed(1)}%`,
      }],
    });
    const frame = host.querySelector('[data-history-scroll]');
    const svg = frame?.querySelector('svg');
    const cursor = host.querySelector('[data-inflation-cursor]');
    const valueLabel = host.querySelector('[data-inflation-value]');
    const dateLabel = host.querySelector('[data-inflation-date]');
    const formatValue = value => Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}%` : '미발표';
    chartUtils.scrollToLatest(frame);
    frame?.addEventListener('pointermove', event => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = (event.clientX - bounds.left) / bounds.width * width;
      const nearest = daily.reduce((closest, row) => Math.abs(x(row) - pointerX) < Math.abs(x(closest) - pointerX) ? row : closest);
      const monthlyAtDate = monthly.filter(row => timestamp(row.month) <= timestamp(nearest.observed_on)).at(-1) || monthly[0];
      const cursorX = x(nearest);
      const labelX = Math.max(170, Math.min(width - 170, cursorX));
      cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX);
      valueLabel.setAttribute('x', labelX); dateLabel.setAttribute('x', cursorX);
      valueLabel.textContent = `통합 ${formatValue(monthlyAtDate[actualKey])} · 선행 ${formatValue(nearest[leadKey])} · 기준 ${formatValue(nearest.policy_rate_upper_pct)} · 실질 ${formatValue(monthlyAtDate[realMonthlyKey])}`;
      dateLabel.textContent = String(nearest.observed_on);
      cursor.classList.add('is-visible'); dateLabel.classList.add('is-visible');
      valueLabel.setAttribute('visibility', 'visible');
    });
    frame?.addEventListener('pointerleave', () => {
      cursor.classList.remove('is-visible'); dateLabel.classList.remove('is-visible');
      valueLabel.setAttribute('visibility', 'hidden');
    });
  }

  async function load() {
    const host = document.getElementById('inflation-model-chart');
    if (!host || !supabaseClient) return;
    const [monthlyResponse, dailyResponse] = await Promise.all([
      chartUtils.loadAllRows((from, to) => supabaseClient.from('us_inflation_monthly')
        .select('month,headline_yoy_pct,core_yoy_pct,policy_rate_upper_pct,headline_real_rate_pct,core_real_rate_pct,status,data_as_of')
        .order('month', { ascending: true }).range(from, to)),
      chartUtils.loadAllRows((from, to) => supabaseClient.from('us_inflation_leading_daily')
        .select('observed_on,target_month,headline_leading_yoy_pct,core_leading_yoy_pct,policy_rate_upper_pct')
        .order('observed_on', { ascending: true }).range(from, to)),
    ]);
    if (monthlyResponse.error || dailyResponse.error) {
      host.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">통합물가 데이터를 불러오지 못했습니다.</div>';
      return;
    }
    state.monthly = monthlyResponse.data || [];
    state.daily = dailyResponse.data || [];
    render();
  }

  function bind(selector, attribute, stateKey) {
    document.querySelectorAll(`${selector} [${attribute}]`).forEach(button => {
      button.addEventListener('click', () => {
        const value = button.getAttribute(attribute);
        if (!value || state[stateKey] === value) return;
        state[stateKey] = value;
        document.querySelectorAll(`${selector} [${attribute}]`).forEach(item => {
          const active = item.getAttribute(attribute) === value;
          item.classList.toggle('is-active', active);
          item.setAttribute('aria-pressed', String(active));
        });
        render();
      });
    });
  }

  renderLegend();
  bind('[data-inflation-kinds]', 'data-inflation-kind', 'kind');
  bind('[data-inflation-ranges]', 'data-inflation-range', 'years');
  window.MacroWatchDashboard?.registerLoader(load);
})();
