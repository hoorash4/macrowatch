(() => {
  'use strict';

  const chartUtils = window.MacroWatchAnalysisChart;
  const supabaseClient = window.macroWatchSupabase || window.MacroWatchFrontend?.createSupabaseClient();
  const state = { years: '5', monthly: [], policy: [] };
  const COLORS = {
    headline: '#0f766e',
    core: '#2563eb',
    policy: '#334155',
  };

  function timestamp(value) {
    return Date.parse(`${String(value)}T00:00:00Z`);
  }

  function formatCursorMonth(value) {
    return String(value).slice(0, 7).replace('-', '.');
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
      chartUtils.legendItem('헤드라인', { stroke: COLORS.headline, width: 3 }),
      chartUtils.legendItem('헤드라인 잠정치', { stroke: COLORS.headline, width: 3, dash: '5 4' }),
      chartUtils.legendItem('코어', { stroke: COLORS.core, width: 3 }),
      chartUtils.legendItem('코어 잠정치', { stroke: COLORS.core, width: 3, dash: '5 4' }),
      chartUtils.legendItem('기준금리', { stroke: COLORS.policy, width: 2.25 }),
    ].join('');
  }

  function renderRealRateSummary() {
    const summary = document.getElementById('inflation-real-rate-summary');
    const latest = state.monthly.at(-1);
    if (!summary || !latest) return;
    const format = value => Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}%` : '미발표';
    const status = latest.status === 'provisional' ? '잠정' : '확정';
    summary.innerHTML = [
      ['헤드라인 실질금리', latest.headline_real_rate_pct],
      ['코어 실질금리', latest.core_real_rate_pct],
    ].map(([label, value]) => `<div class="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs"><span class="text-slate-500">${label}</span><strong class="text-sm text-slate-700">${format(value)} <small class="font-medium text-slate-400">${status}</small></strong></div>`).join('');
  }

  function render() {
    const host = document.getElementById('inflation-model-chart');
    if (!host) return;
    const monthly = state.monthly;
    const policy = state.policy;
    if (!monthly.length || !policy.length) {
      host.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">저장된 통합물가 데이터가 없습니다.</div>';
      return;
    }

    const allDates = [
      ...monthly.map(row => timestamp(row.month)),
      ...policy.map(row => timestamp(row.observed_on)),
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
      ...monthly.flatMap(row => [
        Number(row.headline_yoy_pct), Number(row.core_yoy_pct),
      ]),
      ...policy.map(row => Number(row.target_upper_pct)),
    ].filter(Number.isFinite);
    const domain = chartUtils.axisDomain(values, { includeZero: true, minimumSpan: 2 });
    const y = value => padding.top + (domain.max - value) / (domain.max - domain.min) * (height - padding.top - padding.bottom);
    const ticks = Array.from({ length: 6 }, (_, index) => domain.max - (domain.max - domain.min) * index / 5);
    const grid = ticks.map(value => `<line x1="${padding.left}" x2="${width - padding.right}" y1="${y(value)}" y2="${y(value)}" stroke="#dbe3ed"${Math.abs(value) < .01 ? ' stroke-width="1.5"' : ' stroke-dasharray="3 4"'}/><text x="${padding.left - 9}" y="${y(value) + 4}" text-anchor="end" fill="#64748b" font-size="10">${value.toFixed(1)}%</text>`).join('');

    const actualRows = monthly.filter(row => row.status === 'final');
    const provisionalRows = monthly.filter(row => row.status === 'provisional');
    const bridgeRows = provisionalRows.length && actualRows.length ? [actualRows.at(-1), ...provisionalRows] : provisionalRows;
    const headlinePath = chartUtils.monotoneSeriesPath(actualRows, row => x(row), row => y(Number(row.headline_yoy_pct)));
    const headlineProvisionalPath = chartUtils.monotoneSeriesPath(bridgeRows, row => x(row), row => y(Number(row.headline_yoy_pct)));
    const corePath = chartUtils.monotoneSeriesPath(actualRows, row => x(row), row => y(Number(row.core_yoy_pct)));
    const coreProvisionalPath = chartUtils.monotoneSeriesPath(bridgeRows, row => x(row), row => y(Number(row.core_yoy_pct)));
    const policyRows = policy.map(row => ({ ...row, target_upper_pct: Number(row.target_upper_pct) }));
    const policyPath = stepPath(policyRows, x, y, 'target_upper_pct');
    const years = [...new Set(monthly.map(row => String(row.month).slice(0, 4)))];
    const guides = years.map(year => {
      const point = monthly.find(row => String(row.month).startsWith(year));
      if (!point) return '';
      return `<line x1="${x(point)}" x2="${x(point)}" y1="${padding.top}" y2="${height - padding.bottom}" stroke="#edf0f4"/><text x="${x(point) + 4}" y="${height - 14}" fill="#64748b" font-size="10">${year}</text>`;
    }).join('');
    host.innerHTML = `<svg class="w-full" style="height:${height}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="헤드라인과 코어 통합물가 확정치·잠정치와 기준금리 추이">${grid}${guides}<path d="${headlinePath}" fill="none" stroke="${COLORS.headline}" stroke-width="3" stroke-linecap="round"/><path d="${headlineProvisionalPath}" fill="none" stroke="${COLORS.headline}" stroke-width="3" stroke-dasharray="5 4" stroke-linecap="round"/><path d="${corePath}" fill="none" stroke="${COLORS.core}" stroke-width="3" stroke-linecap="round"/><path d="${coreProvisionalPath}" fill="none" stroke="${COLORS.core}" stroke-width="3" stroke-dasharray="5 4" stroke-linecap="round"/><path d="${policyPath}" fill="none" stroke="${COLORS.policy}" stroke-width="2.25"/><rect data-inflation-hit x="${padding.left}" y="${padding.top}" width="${width - padding.left - padding.right}" height="${height - padding.top - padding.bottom}" fill="transparent"/><line data-inflation-cursor x1="${width - padding.right}" x2="${width - padding.right}" y1="${padding.top}" y2="${height - padding.bottom}" class="policy-expectation-cursor"/><text data-inflation-value x="0" y="16" text-anchor="middle" fill="#334155" font-size="10" font-weight="700" visibility="hidden"></text><text data-inflation-date x="0" y="${height - padding.bottom + 14}" text-anchor="middle" class="policy-expectation-cursor-detail"></text></svg>`;
    chartUtils.scrollableSvg(host.querySelector('svg'), width, baseWidth, {
      top: padding.top,
      bottom: height - padding.bottom,
      axes: [{
        side: 'left', y,
        points: [
          ...monthly.flatMap(row => [
            { x: x(row), value: Number(row.headline_yoy_pct) },
            { x: x(row), value: Number(row.core_yoy_pct) },
          ]),
          ...policy.map(row => ({ x: x(row), value: Number(row.target_upper_pct) })),
        ],
        selector: `path[stroke="${COLORS.headline}"],path[stroke="${COLORS.core}"],path[stroke="${COLORS.policy}"]`,
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
      const nearest = monthly.reduce((closest, row) => Math.abs(x(row) - pointerX) < Math.abs(x(closest) - pointerX) ? row : closest);
      const cursorX = x(nearest);
      const labelX = Math.max(170, Math.min(width - 170, cursorX));
      cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX);
      valueLabel.setAttribute('x', labelX); dateLabel.setAttribute('x', cursorX);
      const status = nearest.status === 'provisional' ? '잠정' : '확정';
      valueLabel.textContent = `헤드라인 ${formatValue(nearest.headline_yoy_pct)} · 코어 ${formatValue(nearest.core_yoy_pct)} (${status}) · 기준 ${formatValue(nearest.policy_rate_upper_pct)}`;
      dateLabel.textContent = formatCursorMonth(nearest.month);
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
    const [monthlyResponse, policyResponse] = await Promise.all([
      chartUtils.loadAllRows((from, to) => supabaseClient.from('us_inflation_monthly')
        .select('month,headline_yoy_pct,core_yoy_pct,policy_rate_upper_pct,headline_real_rate_pct,core_real_rate_pct,status,data_as_of')
        .order('month', { ascending: true }).range(from, to)),
      chartUtils.loadAllRows((from, to) => supabaseClient.from('us_policy_rate_daily')
        .select('observed_on,target_upper_pct')
        .order('observed_on', { ascending: true }).range(from, to)),
    ]);
    if (monthlyResponse.error || policyResponse.error) {
      host.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">통합물가 데이터를 불러오지 못했습니다.</div>';
      return;
    }
    state.monthly = monthlyResponse.data || [];
    state.policy = policyResponse.data || [];
    renderRealRateSummary();
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
  bind('[data-inflation-ranges]', 'data-inflation-range', 'years');
  window.MacroWatchDashboard?.registerLoader(load);
})();
