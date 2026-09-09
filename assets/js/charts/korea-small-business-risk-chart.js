(() => {
  'use strict';
  const utils = window.MacroWatchAnalysisChart;
  const supabaseClient = window.macroWatchSupabase || window.MacroWatchFrontend?.createSupabaseClient();
  const state = { years: '2', rows: [] };
  const COLORS = {
    risk: '#b4535d',
    headline: '#64748b',
  };
  const BASE_WIDTH = 920;
  const HEIGHT = 300;
  const PADDING = { left: utils.axisGutter + 6, right: utils.axisGutter + 6, top: 24, bottom: 42 };
  const timestamp = value => Date.parse(`${String(value)}T00:00:00Z`);
  const monthLabel = value => String(value).slice(0, 7).replace('-', '.');

  function renderLegend() {
    const legend = document.getElementById('korea-small-business-risk-legend');
    if (legend) legend.innerHTML = [
      utils.legendItem('중소기업 위험지수', { stroke: COLORS.risk, width: utils.lineWidths.primary }),
      utils.legendItem('중소기업 위험지수 잠정치', { stroke: COLORS.risk, width: utils.lineWidths.primary, dash: '5 4' }),
      utils.legendItem('중소기업 경기전망 SBHI(역)', { stroke: COLORS.headline, width: utils.lineWidths.comparison }),
    ].join('');
  }

  function render() {
    const host = document.getElementById('korea-small-business-risk-chart');
    if (!host) return;
    if (!state.rows.length) {
      host.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">저장된 중소기업 위험지수가 없습니다.</div>';
      return;
    }
    const rows = utils.rowsForRecentHistory(state.rows, 'month', state.years === '10' ? 10 : state.years);
    const first = timestamp(rows[0].month), last = timestamp(rows.at(-1).month);
    const width = utils.historyWidth(rows, 'month', state.years, BASE_WIDTH);
    const x = row => PADDING.left + (timestamp(row.month) - first) / Math.max(1, last - first) * (width - PADDING.left - PADDING.right);
    const riskValues = rows.map(row => Number(row.risk_index)).filter(Number.isFinite);
    const headlineValues = rows.map(row => Number(row.headline_outlook_sbhi)).filter(Number.isFinite);
    const riskDomain = utils.axisDomain(riskValues, { minimumSpan: 10 });
    const headlineDomain = utils.axisDomain(headlineValues, { minimumSpan: 5 });
    const riskY = value => PADDING.top + (riskDomain.max - value) / (riskDomain.max - riskDomain.min) * (HEIGHT - PADDING.top - PADDING.bottom);
    const headlineY = value => PADDING.top + (value - headlineDomain.min) / (headlineDomain.max - headlineDomain.min) * (HEIGHT - PADDING.top - PADDING.bottom);
    const riskTicks = Array.from({ length: 5 }, (_, index) => riskDomain.max - (riskDomain.max - riskDomain.min) * index / 4);
    const headlineTicks = Array.from({ length: 5 }, (_, index) => headlineDomain.max - (headlineDomain.max - headlineDomain.min) * index / 4);
    const grid = riskTicks.map(value => `<line x1="${PADDING.left}" x2="${width - PADDING.right}" y1="${riskY(value)}" y2="${riskY(value)}" stroke="#e2e8f0" stroke-dasharray="3 4"/><text data-chart-left-axis x="${PADDING.left - 9}" y="${riskY(value) + 4}" text-anchor="end" fill="#64748b" font-size="10">${value.toFixed(0)}</text>`).join('');
    const rightAxis = headlineTicks.map(value => `<text data-chart-right-axis x="${width - PADDING.right + 9}" y="${headlineY(value) + 4}" text-anchor="start" fill="#64748b" font-size="10">${value.toFixed(0)}</text>`).join('');
    const years = [...new Set(rows.map(row => String(row.month).slice(0, 4)))];
    const guides = years.map(year => {
      const point = rows.find(row => String(row.month).startsWith(year));
      if (!point) return '';
      const pointX = x(point);
      const guide = pointX > 65 ? `<line x1="${pointX}" x2="${pointX}" y1="${PADDING.top}" y2="${HEIGHT - PADDING.bottom}" stroke="#edf0f4"/>` : '';
      return `${guide}<text x="${pointX}" y="${HEIGHT - 14}" text-anchor="middle" fill="#64748b" font-size="10">${year}</text>`;
    }).join('');
    const riskPoints = rows.map(row => ({ x: x(row), y: riskY(Number(row.risk_index)) }));
    const riskSegments = utils.monotonePathSegments(
      riskPoints,
      rows.map(row => row.is_provisional ? 'provisional' : 'final'),
    );
    const riskPaths = riskSegments.map(segment => `<path data-korea-small-business-risk-line d="${segment.path}" fill="none" stroke="${COLORS.risk}" stroke-width="${utils.lineWidths.primary}"${segment.key === 'provisional' ? ' stroke-dasharray="5 4"' : ''} stroke-linecap="round"/>`).join('');
    const headlineRows = rows.filter(row => Number.isFinite(Number(row.headline_outlook_sbhi)));
    const headlinePath = utils.monotoneSeriesPath(headlineRows, x, row => headlineY(Number(row.headline_outlook_sbhi)));
    host.innerHTML = `<svg class="w-full" style="height:${HEIGHT}px" viewBox="0 0 ${width} ${HEIGHT}" role="img" aria-label="한국 중소기업 위험지수와 역방향 중소기업 경기전망 SBHI 월별 추이">${grid}${rightAxis}${guides}${riskPaths}<path data-small-business-headline-line d="${headlinePath}" fill="none" stroke="${COLORS.headline}" stroke-width="${utils.lineWidths.comparison}" stroke-linecap="round"/><rect data-korea-small-business-risk-hit x="${PADDING.left}" y="${PADDING.top}" width="${width - PADDING.left - PADDING.right}" height="${HEIGHT - PADDING.top - PADDING.bottom}" fill="transparent"/><line data-korea-small-business-risk-cursor x1="0" x2="0" y1="${PADDING.top}" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/><text data-korea-small-business-risk-value x="0" y="16" text-anchor="middle" fill="#334155" font-size="10" font-weight="700" visibility="hidden"></text><text data-korea-small-business-risk-date x="0" y="${HEIGHT - PADDING.bottom + 14}" text-anchor="middle" class="policy-expectation-cursor-detail"></text></svg>`;
    utils.scrollableSvg(host.querySelector('svg'), width, BASE_WIDTH, {
      left: PADDING.left,
      right: PADDING.right,
      top: PADDING.top,
      bottom: HEIGHT - PADDING.bottom,
      axes: [{
        side: 'left',
        y: riskY,
        points: rows.map(row => ({ x: x(row), value: Number(row.risk_index) })),
        selector: '[data-korea-small-business-risk-line]',
        format: value => value.toFixed(0),
      }, {
        side: 'right',
        y: headlineY,
        points: headlineRows.map(row => ({ x: x(row), value: Number(row.headline_outlook_sbhi) })),
        selector: '[data-small-business-headline-line]',
        format: value => value.toFixed(0),
      }],
    });
    const frame = host.querySelector('[data-history-scroll]');
    const svg = frame?.querySelector('svg');
    frame?.addEventListener('pointermove', event => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = (event.clientX - bounds.left) / bounds.width * width;
      const nearest = rows.reduce((closest, row) => Math.abs(x(row) - pointerX) < Math.abs(x(closest) - pointerX) ? row : closest);
      const cursorX = x(nearest);
      const cursor = host.querySelector('[data-korea-small-business-risk-cursor]');
      cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX); cursor.classList.add('is-visible');
      const value = host.querySelector('[data-korea-small-business-risk-value]');
      value.setAttribute('x', Math.max(130, Math.min(width - 130, cursorX))); value.setAttribute('visibility', 'visible');
      const labels = [`위험 ${Number(nearest.risk_index).toFixed(1)}`];
      if (Number.isFinite(Number(nearest.headline_outlook_sbhi))) labels.push(`경기전망 ${Number(nearest.headline_outlook_sbhi).toFixed(1)}`);
      if (nearest.is_provisional) labels.push('잠정치');
      value.textContent = labels.join(' · ');
      const date = host.querySelector('[data-korea-small-business-risk-date]');
      date.setAttribute('x', cursorX); date.textContent = monthLabel(nearest.month); date.classList.add('is-visible');
    });
    frame?.addEventListener('pointerleave', () => {
      host.querySelector('[data-korea-small-business-risk-cursor]')?.classList.remove('is-visible');
      host.querySelector('[data-korea-small-business-risk-date]')?.classList.remove('is-visible');
      host.querySelector('[data-korea-small-business-risk-value]')?.setAttribute('visibility', 'hidden');
    });
    utils.scrollToLatest(frame);
  }

  async function load() {
    const host = document.getElementById('korea-small-business-risk-chart');
    if (!host || !supabaseClient) return;
    const { data, error } = await utils.loadAllRows((from, to) => supabaseClient
      .from('kr_small_business_risk_monthly')
      .select('month,risk_index,headline_outlook_sbhi,is_provisional')
      .order('month', { ascending: true })
      .range(from, to));
    if (error) {
      host.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">중소기업 위험지수를 불러오지 못했습니다.</div>';
      return;
    }
    state.rows = data || [];
    render();
  }

  document.querySelectorAll('[data-korea-small-business-risk-range]').forEach(button => button.addEventListener('click', () => {
    const years = button.dataset.koreaSmallBusinessRiskRange;
    if (!years || years === state.years) return;
    state.years = years;
    document.querySelectorAll('[data-korea-small-business-risk-range]').forEach(item => {
      const active = item.dataset.koreaSmallBusinessRiskRange === years;
      item.classList.toggle('is-active', active);
      item.setAttribute('aria-pressed', String(active));
    });
    render();
  }));

  renderLegend();
  window.MacroWatchDashboard?.registerLoader(load);
})();
