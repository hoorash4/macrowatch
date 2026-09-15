(() => {
  'use strict';
  // 화면 상태만 담당하고, 요청 번호로 늦게 도착한 이전 지수 응답을 무시합니다.
  const host = document.getElementById('historical-chart-host');
  if (!host) return;
  const status = document.getElementById('historical-chart-status');
  const meta = document.getElementById('historical-chart-meta');
  const message = document.getElementById('historical-chart-message');
  const retry = document.getElementById('historical-chart-retry');
  const fullRange = document.getElementById('historical-chart-full-range');
  const buttons = [...document.querySelectorAll('[data-historical-index]')];
  const data = window.MacroWatchHistoricalData;
  let activeCode = buttons.find(button => button.getAttribute('aria-selected') === 'true')?.dataset.historicalIndex;
  let requestToken = 0;
  let repository;
  let chart;

  function state(kind, text) {
    host.dataset.state = kind;
    host.setAttribute('aria-busy', String(kind === 'loading'));
    status.textContent = text;
    message.textContent = kind === 'ready' ? '' : text;
    message.hidden = kind === 'ready';
    retry.hidden = kind !== 'error';
    fullRange.disabled = kind !== 'ready';
  }
  async function render(code) {
    const token = ++requestToken;
    activeCode = code;
    for (const button of buttons) {
      const selected = button.dataset.historicalIndex === code;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-selected', String(selected));
    }
    meta.textContent = `${data?.indices[code] || code} · 1990년 이후 일간 종가`;
    state('loading', '지수 데이터 불러오는 중');
    try {
      if (!data || !window.MacroWatchHistoricalChart || !window.MacroWatchFrontend) throw new Error('화면 모듈을 불러오지 못했습니다.');
      if (!repository) {
        const client = window.macroWatchSupabase || window.MacroWatchFrontend.createSupabaseClient();
        if (!client) throw new Error('데이터 연결을 확인해 주세요.');
        window.macroWatchSupabase = client;
        repository = data.createRepository(client);
      }
      if (!chart) chart = window.MacroWatchHistoricalChart.create(host);
      chart.setData([]);
      const rows = await repository.load(code);
      if (token !== requestToken) return;
      chart.setData(rows);
      if (!rows.length) { state('empty', '저장된 지수 데이터가 없습니다.'); return; }
      const count = window.MacroWatchFrontend.formatDisplayNumber(rows.length, { locale: 'ko-KR' });
      state('ready', `${count}개 · ${rows[0].time} ~ ${rows.at(-1).time}`);
    } catch (error) {
      if (token !== requestToken) return;
      chart?.destroy();
      chart = null;
      state('error', '지수 데이터를 불러오지 못했습니다. 다시 시도해 주세요.');
      console.error('[Historical Insight]', error);
    }
  }
  buttons.forEach(button => button.addEventListener('click', () => render(button.dataset.historicalIndex)));
  retry.addEventListener('click', () => render(activeCode));
  fullRange.addEventListener('click', () => chart?.fit());
  window.addEventListener('pagehide', () => { ++requestToken; chart?.destroy(); chart = null; });
  window.addEventListener('pageshow', event => { if (event.persisted) render(activeCode); });
  render(activeCode);
})();
