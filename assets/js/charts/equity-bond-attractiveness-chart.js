(() => {
  'use strict';
  const utils = window.MacroWatchAnalysisChart;
  const { lineWidths } = utils;
  const card = document.querySelector('#equity-bond-attractiveness-dashboard');
  if (!card || !utils) return;
  const host = card.querySelector('[data-equity-bond-chart]');
  const state = { rows: [], years: 2 };
  const HEIGHT = 320, BASE_WIDTH = 920, LEFT = 14, RIGHT = 24, TOP = 28, BOTTOM = 42, AXIS = 46;
  const COLORS = { KR: '#2563a8', US: '#b7791f' };
  const LABELS = { KR: '한국(KOSPI 100)', US: '미국(S&P 100)' };
  const scale = (value, min, max, from, to) => max === min ? (from + to) / 2 : from + (value - min) / (max - min) * (to - from);

  function timelineGuides(dates, first, last, width) {
    let previousKey = '';
    return dates.map(value => {
      const observed = new Date(`${value}T00:00:00Z`);
      const year = observed.getUTCFullYear();
      const key = String(year);
      if (key === previousKey) return '';
      previousKey = key;
      const x = scale(Date.parse(value), first, last, LEFT, width - RIGHT);
      return `<line x1="${x}" x2="${x}" y1="${TOP}" y2="${HEIGHT - BOTTOM}" class="policy-expectation-year-guide"/><text x="${x}" y="${HEIGHT - 10}" text-anchor="middle" class="policy-expectation-year">${year}</text>`;
    }).join('');
  }

  function render() {
    if (!state.rows.length) { host.textContent = '아직 저장된 상대매력 자료가 없습니다.'; return; }
    const plotted = utils.rowsForRecentHistory(state.rows, 'observation_date', state.years);
    const dates = [...new Set(plotted.map(row => row.observation_date))].sort();
    const first = Date.parse(dates[0]), last = Date.parse(dates.at(-1));
    const viewport = Math.max(680, (host.clientWidth || BASE_WIDTH) - AXIS);
    const width = utils.timelineWidth(viewport, first, last, state.years);
    const domain = { min: 0, max: 100 };
    const byCountry = Object.fromEntries(['KR', 'US'].map(country => [country, plotted.filter(row => row.country === country).map(row => ({
      ...row,
      x: scale(Date.parse(row.observation_date), first, last, LEFT, width - RIGHT),
      y: scale(Number(row.score), domain.min, domain.max, HEIGHT - BOTTOM, TOP),
    }))]));
    const ticks = [0, 25, 50, 75, 100];
    const axis = ticks.map(value => `<text x="${AXIS - 8}" y="${scale(value, domain.min, domain.max, HEIGHT - BOTTOM, TOP) + 3}" text-anchor="end" fill="#64748b" font-size="11">${value}</text>`).join('');
    const grid = ticks.map(value => `<line x1="${LEFT}" x2="${width - RIGHT}" y1="${scale(value, domain.min, domain.max, HEIGHT - BOTTOM, TOP)}" y2="${scale(value, domain.min, domain.max, HEIGHT - BOTTOM, TOP)}" stroke="${value === 50 ? '#94a3b8' : '#e2e8f0'}" ${value === 50 ? 'stroke-dasharray="5 4"' : ''}/>`).join('');
    const paths = ['KR', 'US'].map(country => `<path d="${utils.monotonePath(byCountry[country])}" fill="none" stroke="${COLORS[country]}" stroke-width="${lineWidths.primary}"/>`).join('');
    const guides = timelineGuides(dates, first, last, width);
    host.innerHTML = `<div class="policy-expectation-chart-layout"><svg class="policy-expectation-y-axis" viewBox="0 0 ${AXIS} ${HEIGHT}" aria-hidden="true">${axis}</svg><div class="policy-expectation-chart-frame" data-equity-bond-frame><svg class="policy-expectation-chart-svg" style="width:${width}px;background:#fff" viewBox="0 0 ${width} ${HEIGHT}" role="img" aria-label="한국과 미국 각각의 주식투자 매력 흐름">${guides}${grid}${paths}<line data-cursor x1="0" x2="0" y1="${TOP}" y2="${HEIGHT - BOTTOM}" class="policy-expectation-cursor"/><text data-value text-anchor="middle" y="16" fill="#334155" font-size="11"></text><text data-date text-anchor="middle" y="${HEIGHT - BOTTOM + 15}" class="policy-expectation-cursor-detail"></text></svg></div></div><div class="equity-bond-legend">${utils.legendItem(LABELS.KR, { stroke: COLORS.KR, width: lineWidths.primary })}${utils.legendItem(LABELS.US, { stroke: COLORS.US, width: lineWidths.primary })}</div>`;
    const frame = host.querySelector('[data-equity-bond-frame]');
    const svg = frame.querySelector('svg');
    const cursor = host.querySelector('[data-cursor]'), value = host.querySelector('[data-value]'), dateLabel = host.querySelector('[data-date]');
    utils.scrollToLatest(frame);
    frame.addEventListener('pointermove', event => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = (event.clientX - bounds.left) / bounds.width * width;
      const nearestDate = dates.reduce((best, current) => Math.abs(scale(Date.parse(current), first, last, LEFT, width - RIGHT) - pointerX) < Math.abs(scale(Date.parse(best), first, last, LEFT, width - RIGHT) - pointerX) ? current : best);
      const x = scale(Date.parse(nearestDate), first, last, LEFT, width - RIGHT);
      const rows = ['KR', 'US'].map(country => state.rows.find(row => row.country === country && row.observation_date === nearestDate)).filter(Boolean);
      cursor.setAttribute('x1', x); cursor.setAttribute('x2', x); cursor.classList.add('is-visible');
      value.setAttribute('x', x); value.textContent = rows.map(row => `${LABELS[row.country]} ${Number(row.score).toFixed(1)}`).join(' · '); value.setAttribute('visibility', 'visible');
      dateLabel.setAttribute('x', x); dateLabel.textContent = nearestDate; dateLabel.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); value.setAttribute('visibility', 'hidden'); dateLabel.classList.remove('is-visible'); });
  }

  async function load({ supabaseClient }) {
    if (!supabaseClient) return;
    const { data, error } = await utils.loadAllRows((from, to) => supabaseClient.from('equity_bond_attractiveness_weekly').select('country,observation_date,score').eq('method_version', 'stock-attractiveness-v3').order('observation_date').order('country').range(from, to));
    if (error) { host.textContent = '상대매력 자료를 불러오지 못했습니다.'; return; }
    state.rows = (data || []).filter(row => Number.isFinite(Number(row.score)));
    render();
  }
  card.querySelector('[data-equity-bond-ranges]').addEventListener('click', event => {
    const button = event.target.closest('[data-years]'); if (!button) return;
    state.years = button.dataset.years;
    card.querySelectorAll('[data-years]').forEach(item => { item.classList.toggle('is-active', item === button); item.setAttribute('aria-pressed', String(item === button)); });
    render();
  });
  window.MacroWatchDashboard?.registerLoader(load);
})();
