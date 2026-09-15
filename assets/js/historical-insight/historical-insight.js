(() => {
  'use strict';
  // 사례 정의, 원시 지수, 차트 어댑터를 조합하는 화면 컨트롤러입니다.
  const $ = id => document.getElementById(id);
  const host = $('historical-chart-host');
  if (!host) return;
  const status = $('historical-chart-status'), meta = $('historical-chart-meta'), message = $('historical-chart-message');
  const retry = $('historical-chart-retry'), fullRange = $('historical-chart-full-range'), caseRange = $('historical-chart-case-range');
  const marketButtons = [...document.querySelectorAll('[data-historical-index]')];
  const indexData = window.MacroWatchHistoricalData, cycleData = window.MacroWatchHistoricalCycles;
  let activeCode = 'NASDAQ_COMPOSITE', activeCase = null, cases = [], requestToken = 0;
  let indexRepository, caseRepository, chart, activeRows = [], currentUser = null, isAdmin = false;

  function state(kind, text) {
    host.dataset.state = kind;
    host.setAttribute('aria-busy', String(kind === 'loading'));
    status.textContent = text;
    message.textContent = kind === 'ready' ? '' : text;
    message.hidden = kind === 'ready';
    retry.hidden = kind !== 'error';
    fullRange.disabled = caseRange.disabled = kind !== 'ready';
  }
  function setMarket(code) {
    activeCode = code;
    for (const button of marketButtons) {
      const selected = button.dataset.historicalIndex === code;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-selected', String(selected));
    }
  }
  function formatPoint(kind, point) {
    const card = document.querySelector(`[data-cycle-point="${kind}"]`);
    if (!card) return;
    card.querySelector('strong').textContent = point ? point.time : '미확정';
    card.querySelector('span').textContent = point ? window.MacroWatchFrontend.formatDisplayNumber(point.value) : '—';
  }
  const percentage = (value, absolute = false) => value == null ? '—' : `${absolute ? '' : value > 0 ? '+' : ''}${window.MacroWatchFrontend.formatDisplayNumber(absolute ? Math.abs(value) : value, { maximumFractionDigits: 1 })}%`;
  const duration = value => value == null ? '—' : `${window.MacroWatchFrontend.formatDisplayNumber(value)}일`;
  function showCycle(item, metrics) {
    $('historical-cycle-panel').hidden = false;
    $('historical-cycle-state').textContent = item.status === 'confirmed' ? 'CONFIRMED CYCLE' : item.status === 'in_progress' ? 'IN PROGRESS' : 'DRAFT';
    $('historical-cycle-name').textContent = item.name;
    $('historical-search-range').textContent = `관찰 범위 ${item.searchStart} ~ ${item.searchEnd || '현재'}`;
    $('historical-cycle-description').textContent = item.summary;
    formatPoint('start', metrics.start); formatPoint('peak', metrics.peak); formatPoint('trough', metrics.trough);
    $('historical-rise').textContent = percentage(metrics.rise);
    $('historical-fall').textContent = percentage(metrics.fall);
    $('historical-drawdown').textContent = percentage(metrics.drawdown, true);
    $('historical-rise-days').textContent = duration(metrics.riseDays);
    $('historical-fall-days').textContent = duration(metrics.fallDays);
    if (isAdmin) {
      $('historical-cycle-editor').hidden = false;
      $('historical-start-date').value = item.startDate || '';
      $('historical-peak-date').value = item.peakDate || '';
      $('historical-trough-date').value = item.troughDate || '';
      $('historical-cycle-save-status').textContent = '';
    }
  }
  function renderCaseList() {
    const root = $('historical-case-list');
    root.replaceChildren();
    for (const item of cases) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'historical-case'; button.dataset.historicalCase = item.code;
      const label = document.createElement('span'), badge = document.createElement('small');
      label.textContent = item.name;
      badge.textContent = item.status === 'confirmed' ? '확정' : item.status === 'in_progress' ? '진행 중' : '초안';
      button.append(label, badge);
      button.addEventListener('click', () => selectCase(item.code));
      root.append(button);
    }
  }
  function activeCaseButton() {
    document.querySelectorAll('[data-historical-case]').forEach(button => button.classList.toggle('is-active', button.dataset.historicalCase === activeCase?.code));
  }
  function focusCase() {
    if (!chart || !activeRows.length || !activeCase) return;
    const inRange = activeRows.filter(row => row.time >= activeCase.searchStart && (!activeCase.searchEnd || row.time <= activeCase.searchEnd));
    if (inRange.length) chart.focus(inRange[0].time, inRange.at(-1).time);
  }
  async function render(code) {
    const token = ++requestToken;
    setMarket(code);
    meta.textContent = `${activeCase?.name || 'Historical Case'} · ${indexData?.indices[code] || code}`;
    state('loading', '사이클 데이터 불러오는 중');
    try {
      if (!indexData || !cycleData || !window.MacroWatchHistoricalChart || !window.MacroWatchFrontend) throw new Error('화면 모듈을 불러오지 못했습니다.');
      if (!chart) chart = window.MacroWatchHistoricalChart.create(host);
      chart.setData([]);
      const [rows, primaryRows] = await Promise.all([indexRepository.load(code), indexRepository.load(activeCase.primaryIndex)]);
      if (token !== requestToken) return;
      const metrics = cycleData.calculate(activeCase, primaryRows);
      activeRows = rows;
      chart.setData(rows);
      if (!rows.length) { state('empty', '저장된 지수 데이터가 없습니다.'); return; }
      chart.setCycle(cycleData.chartPoints(activeCase, rows));
      focusCase();
      showCycle(activeCase, metrics);
      const count = window.MacroWatchFrontend.formatDisplayNumber(rows.length, { locale: 'ko-KR' });
      state('ready', `${count}개 · ${rows[0].time} ~ ${rows.at(-1).time}`);
    } catch (error) {
      if (token !== requestToken) return;
      chart?.destroy(); chart = null; activeRows = [];
      state('error', '사이클 데이터를 불러오지 못했습니다. 다시 시도해 주세요.');
      console.error('[Historical Insight]', error);
    }
  }
  function selectCase(code) {
    const item = cases.find(candidate => candidate.code === code);
    if (!item) return;
    activeCase = item; activeCaseButton();
    render(item.primaryIndex);
  }
  async function initialize() {
    try {
      if (!indexData || !cycleData || !window.MacroWatchFrontend) throw new Error('화면 모듈을 불러오지 못했습니다.');
      const client = window.macroWatchSupabase || window.MacroWatchFrontend.createSupabaseClient();
      if (!client) throw new Error('데이터 연결을 확인해 주세요.');
      window.macroWatchSupabase = client;
      indexRepository = indexData.createRepository(client); caseRepository = cycleData.createRepository(client);
      const { data: authData, error: authError } = await client.auth.getSession();
      if (authError) throw authError;
      currentUser = authData.session?.user || null;
      if (!currentUser) throw new Error('로그인이 필요합니다.');
      const [{ data: account, error: accountError }, loadedCases] = await Promise.all([
        client.from('user_accounts').select('is_admin').eq('user_id', currentUser.id).maybeSingle(), caseRepository.load(),
      ]);
      isAdmin = !accountError && account?.is_admin === true;
      cases = loadedCases;
      if (!cases.length) { state('empty', '저장된 Historical Case가 없습니다.'); return; }
      renderCaseList(); selectCase(cases[0].code);
    } catch (error) {
      state('error', 'Historical Case를 불러오지 못했습니다. 다시 시도해 주세요.');
      console.error('[Historical Insight]', error);
    }
  }
  marketButtons.forEach(button => button.addEventListener('click', () => activeCase && render(button.dataset.historicalIndex)));
  $('historical-cycle-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!isAdmin || !activeCase) return;
    const output = $('historical-cycle-save-status');
    output.textContent = '저장 중';
    try {
      const values = { startDate: $('historical-start-date').value, peakDate: $('historical-peak-date').value,
        troughDate: $('historical-trough-date').value };
      cycleData.calculate({ ...activeCase, ...values }, await indexRepository.load(activeCase.primaryIndex));
      const saved = await caseRepository.save(activeCase.code, values, currentUser.id);
      cases = cases.map(item => item.code === saved.code ? saved : item);
      activeCase = saved; renderCaseList(); activeCaseButton(); await render(activeCode);
      output.textContent = '저장 완료';
    } catch (error) {
      output.textContent = `저장 오류: ${error?.message || '알 수 없는 오류'}`;
    }
  });
  retry.addEventListener('click', () => activeCase ? render(activeCode) : initialize());
  fullRange.addEventListener('click', () => chart?.fit());
  caseRange.addEventListener('click', focusCase);
  window.addEventListener('pagehide', () => { ++requestToken; chart?.destroy(); chart = null; });
  window.addEventListener('pageshow', event => { if (event.persisted) activeCase ? render(activeCode) : initialize(); });
  initialize();
})();
