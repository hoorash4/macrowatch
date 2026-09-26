/* eslint-disable no-console */
/**
 * MacroWatch Historical Insight
 * 과거 유사 국면 지수 및 지표 분석 모듈
 */
(function() {
  'use strict';

  const $ = id => document.getElementById(id);
  const $$ = selector => document.querySelectorAll(selector);

  let chart = null;
  let activeCase = null;
  let activeCode = 'NASDAQ_COMPOSITE';
  let activeMode = 'history';
  let activeHistoricalCode = null;
  let expandedHistoricalCode = null;
  let hiddenIndicatorCodes = new Set();
  let currentUser = null;
  let isAdmin = false;
  let currentModel = null;
  let currentModelStatus = 'none';
  let cases = [];
  let indexRepository = null;
  let indicatorRepository = null;
  let visibleIndicators = [];
  let activeIndicatorContext = null;
  let manualPivotContext = null;
  let manualReasonChoices = [];
  let manualReasonLoadError = '';
  let requestToken = 0;
  let activeRows = [];
  let pendingCurrentSave = null;
  let client = null;
  let functionClient = null;

  const analysisCache = new Map();
  const host = $('historical-index-chart');
  const meta = $('historical-chart-meta');
  const rangeInfo = $('historical-range-info');
  const statusEl = $('historical-status-text');
  const modeButtons = $$('.historical-mode-button');
  const marketButtons = $$('.historical-market-pill');
  const indexData = window.MacroWatchHistoricalIndexData;
  const cycleData = window.MacroWatchHistoricalCycleData;
  const indicatorAnalysis = window.MacroWatchHistoricalIndicatorAnalysis;
  const selection = window.MacroWatchHistoricalIndicatorSelection?.createManager();

  const isHistoricalCase = item => item && item.code !== 'current_cycle';

  function state(kind, message) {
    if (!statusEl) return;
    statusEl.textContent = message || '';
    statusEl.dataset.state = kind;
  }

  function indicatorLoading(message = '비교 지표 불러오는 중') {
    const root = $('historical-indicator-list');
    const detail = $('historical-indicator-detail');
    if (root) {
      root.replaceChildren();
      const p = document.createElement('p');
      p.className = 'historical-indicator-empty';
      p.textContent = message;
      root.append(p);
    }
    if (detail) {
      detail.hidden = true;
      detail.replaceChildren();
    }
  }

  function setMode(mode) {
    activeMode = mode;
    for (const button of modeButtons) {
      const active = button.dataset.historicalMode === mode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
    const currentNameSection = $('historical-current-name-section');
    if (currentNameSection) {
      currentNameSection.hidden = mode !== 'current';
    }
    $('historical-case-add').hidden = !isAdmin || mode !== 'history';
    $('historical-current-cycle-edit').hidden = !isAdmin || mode !== 'current';
    $('historical-indicator-clear').hidden = mode === 'current';
    $('historical-indicator-search').hidden = mode === 'current';
    closeCurrentNameEditor();
    closeCaseModal();
  }

  function setMarket(code) {
    activeCode = code;
    for (const button of marketButtons) {
      const active = button.dataset.historicalIndex === code;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
  }

  function activeCaseButton() {
    for (const button of $$('.historical-case-item')) {
      const active = button.dataset.caseCode === activeCase?.code;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
  }

  function focusCase() {
    if (!chart || !activeCase) return;
    const paddingDays = activeMode === 'history' ? 40 : 15;
    chart.focusRange(activeCase.searchStart, activeCase.searchEnd, paddingDays);
  }

  function showCycle(item, cycle, metrics) {
    const name = $('historical-cycle-name');
    const summary = $('historical-cycle-summary');
    const range = $('historical-cycle-range');
    const start = $('historical-cycle-start');
    const peak = $('historical-cycle-peak');
    const trough = $('historical-cycle-trough');
    const decline = $('historical-cycle-decline');
    const duration = $('historical-cycle-duration');

    name.textContent = item.name;
    summary.textContent = item.summary;
    range.textContent = `분석 구간: ${item.searchStart} ~ ${item.searchEnd}`;
    start.textContent = cycle.startDate ? `저점 ${cycle.startDate}` : '저점 미지정';
    peak.textContent = cycle.peakDate ? `고점 ${cycle.peakDate}` : '고점 미지정';
    trough.textContent = cycle.troughDate ? `저점 ${cycle.troughDate}` : '저점 미지정';
    decline.textContent = metrics?.maxDeclineRate != null ? `최대 하락률 ${metrics.maxDeclineRate}%` : '최대 하락률 --';
    duration.textContent = metrics?.durationDays != null ? `하락 기간 ${metrics.durationDays}일` : '하락 기간 --';
    rangeInfo.textContent = `${item.name} · ${indexData.indices[activeCode] || activeCode} · ${item.searchStart} ~ ${item.searchEnd}`;
  }

  function renderCases() {
    const list = $('historical-case-list');
    list.replaceChildren();

    if (activeMode === 'current') {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'historical-case-item is-current';
      button.dataset.caseCode = currentModel.code;
      const title = document.createElement('strong');
      title.textContent = currentModel.name;
      const desc = document.createElement('span');
      desc.textContent = currentModel.summary;
      button.append(title, desc);
      button.addEventListener('click', () => selectCase(currentModel.code));
      list.append(button);
      activeCaseButton();
      return;
    }

    const items = cases.filter(isHistoricalCase);
    for (const item of items) {
      const wrapper = document.createElement('div');
      wrapper.className = 'historical-case-card-wrapper';
      const isExpanded = item.code === expandedHistoricalCode;
      wrapper.classList.toggle('is-expanded', isExpanded);

      const header = document.createElement('div');
      header.className = 'historical-case-card-header';

      const titleBtn = document.createElement('button');
      titleBtn.type = 'button';
      titleBtn.className = 'historical-case-item-title-btn';
      titleBtn.dataset.caseCode = item.code;
      const title = document.createElement('strong');
      title.textContent = item.name;
      titleBtn.append(title);
      titleBtn.addEventListener('click', () => selectCase(item.code));

      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'historical-case-toggle-btn';
      toggleBtn.setAttribute('aria-expanded', String(isExpanded));
      toggleBtn.setAttribute('aria-label', `${item.name} 세부정보 토글`);
      const icon = document.createElement('i');
      icon.className = `fa-solid ${isExpanded ? 'fa-chevron-up' : 'fa-chevron-down'}`;
      toggleBtn.append(icon);
      toggleBtn.addEventListener('click', () => {
        expandedHistoricalCode = isExpanded ? null : item.code;
        renderCases();
      });

      header.append(titleBtn, toggleBtn);
      wrapper.append(header);

      if (isExpanded) {
        const body = document.createElement('div');
        body.className = 'historical-case-card-body';
        const summary = document.createElement('p');
        summary.className = 'historical-case-card-summary';
        summary.textContent = item.summary;
        body.append(summary);

        const marketsList = document.createElement('ul');
        marketsList.className = 'historical-case-markets-list';
        const order = ['NASDAQ_COMPOSITE', 'SP500', 'KOSPI'];
        for (const code of order) {
          const m = item.markets?.[code];
          if (!m) continue;
          const li = document.createElement('li');
          const marketName = document.createElement('b');
          marketName.textContent = indexData.indices[code] || code;
          const dates = document.createElement('span');
          const pts = [];
          if (m.startDate) pts.push(`저점 ${m.startDate}`);
          if (m.peakDate) pts.push(`고점 ${m.peakDate}`);
          if (m.troughDate) pts.push(`저점 ${m.troughDate}`);
          dates.textContent = pts.join(' → ') || '사이클 미등록';
          li.append(marketName, dates);
          marketsList.append(li);
        }
        body.append(marketsList);

        if (isAdmin) {
          const editBtn = document.createElement('button');
          editBtn.type = 'button';
          editBtn.className = 'historical-case-edit-btn';
          editBtn.textContent = '수정';
          editBtn.addEventListener('click', () => openCaseModal(item));
          body.append(editBtn);
        }
        wrapper.append(body);
      }

      list.append(wrapper);
    }
    activeCaseButton();
  }

  function renderCaseCycleRows(cycles = {}) {
    const root = $('historical-case-cycle-rows');
    root.replaceChildren();
    for (const code of ['NASDAQ_COMPOSITE', 'SP500', 'KOSPI']) {
      const cycle = cycles[code] || {};
      const row = document.createElement('div');
      row.className = 'historical-case-cycle-row';
      row.dataset.caseCycleIndex = code;

      const title = document.createElement('strong');
      title.textContent = indexData.indices[code] || code;
      row.append(title);

      for (const [key, label] of [['startDate', 'START'], ['peakDate', 'PEAK'], ['troughDate', 'TROUGH']]) {
        const field = document.createElement('label');
        const input = document.createElement('input');
        field.textContent = label;
        input.type = 'date';
        input.dataset.caseCycleField = key;
        input.value = cycle[key] || '';
        field.append(input);
        row.append(field);
      }
      root.append(row);
    }
  }

  function closeCaseModal() {
    $('historical-case-modal').hidden = true;
    $('historical-case-save-status').textContent = '';
    $('historical-case-auto-status').textContent = '';
  }

  function openCaseModal(item = null) {
    if (!isAdmin) return;
    $('historical-case-modal-title').textContent = item ? '과거 국면 수정' : '과거 국면 추가';
    $('historical-case-code').value = item?.code || '';
    $('historical-case-name').value = item?.name || '';
    $('historical-case-primary-index').value = item?.primaryIndex || 'NASDAQ_COMPOSITE';
    $('historical-case-search-start').value = item?.searchStart || '';
    $('historical-case-search-end').value = item?.searchEnd || '';
    $('historical-case-summary').value = item?.summary || '';
    renderCaseCycleRows(item?.markets || {});
    $('historical-case-save-status').textContent = '';
    $('historical-case-auto-status').textContent = '';
    $('historical-case-modal').hidden = false;
    $('historical-case-name').focus();
  }

  function caseCyclesFromForm() {
    return [...document.querySelectorAll('[data-case-cycle-index]')].map(row => ({
      index_code: row.dataset.caseCycleIndex,
      start_date: row.querySelector('[data-case-cycle-field="startDate"]')?.value || null,
      peak_date: row.querySelector('[data-case-cycle-field="peakDate"]')?.value || null,
      trough_date: row.querySelector('[data-case-cycle-field="troughDate"]')?.value || null,
    }));
  }

  function applyCyclesToForm(cycles) {
    for (const [code, cycle] of Object.entries(cycles || {})) {
      const row = document.querySelector(`[data-case-cycle-index="${code}"]`);
      if (!row) continue;
      const start = row.querySelector('[data-case-cycle-field="startDate"]');
      const peak = row.querySelector('[data-case-cycle-field="peakDate"]');
      const trough = row.querySelector('[data-case-cycle-field="troughDate"]');
      if (start && cycle.startDate) start.value = cycle.startDate;
      if (peak && cycle.peakDate) peak.value = cycle.peakDate;
      if (trough && cycle.troughDate) trough.value = cycle.troughDate;
    }
  }

  async function calculateAutoCycles() {
    if (!isAdmin || !functionClient) return;
    const start = $('historical-case-search-start').value;
    const end = $('historical-case-search-end').value;
    const primaryIndex = $('historical-case-primary-index').value || 'NASDAQ_COMPOSITE';
    const status = $('historical-case-auto-status');
    const saveStatus = $('historical-case-save-status');
    const autoBtn = $('historical-case-auto-calc');

    if (!start || !end) {
      status.textContent = '조회 시작일과 종료일을 먼저 입력해 주세요.';
      return;
    }

    autoBtn.disabled = true;
    status.textContent = '지수별 기준점 계산 중';
    saveStatus.textContent = '';

    try {
      const cycles = {};
      for (const code of ['NASDAQ_COMPOSITE', 'SP500', 'KOSPI']) {
        const rows = await indexRepository.load(code);
        const filtered = rows.filter(row => row.time >= start && row.time <= end);
        if (filtered.length < 5) continue;
        const result = cycleData.detectCycle(filtered);
        if (result) cycles[code] = result;
      }
      applyCyclesToForm(cycles);
      status.textContent = '기준점을 자동으로 계산했습니다. 필요시 날짜를 직접 수정한 뒤 저장해 주세요.';
    } catch (error) {
      status.textContent = '기준점 자동 계산 실패: ' + (error?.message || '알 수 없는 오류');
      console.error('[Historical auto cycles]', error);
    } finally {
      autoBtn.disabled = false;
    }
  }

  async function saveCase(event) {
    event.preventDefault();
    if (!isAdmin || !functionClient) return;

    const saveBtn = $('historical-case-save');
    const status = $('historical-case-save-status');
    const code = $('historical-case-code').value.trim();
    const name = $('historical-case-name').value.trim();
    const primaryIndex = $('historical-case-primary-index').value;
    const searchStart = $('historical-case-search-start').value;
    const searchEnd = $('historical-case-search-end').value;
    const summary = $('historical-case-summary').value.trim();
    const cycles = caseCyclesFromForm();

    if (!name || !searchStart || !searchEnd || !summary) {
      status.textContent = '모든 항목을 입력해 주세요.';
      return;
    }

    saveBtn.disabled = true;
    status.textContent = '저장 중';

    try {
      const generatedCode = code || name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || `case_${Date.now()}`;
      await functionClient.invoke('admin-control', {
        action: 'save_historical_case',
        case_code: generatedCode,
        name,
        primary_index: primaryIndex,
        search_start: searchStart,
        search_end: searchEnd,
        summary,
        cycles: cycles.filter(item => item.start_date || item.peak_date || item.trough_date)
      });

      const repository = cycleData.createRepository(client);
      cases = await repository.loadCases();
      const nextActive = cases.find(candidate => candidate.code === generatedCode) || cases[0];
      expandedHistoricalCode = generatedCode;
      closeCaseModal();
      renderCases();
      if (nextActive) await selectCase(nextActive.code);
    } catch (error) {
      status.textContent = '저장 오류: ' + (error?.message || '알 수 없는 오류');
      console.error('[Historical case save]', error);
    } finally {
      saveBtn.disabled = false;
    }
  }

  function openCurrentNameEditor() {
    if (!isAdmin) return;
    const form = $('historical-current-name-form');
    $('historical-current-name-input').value = currentModel.name;
    $('historical-current-name-status').textContent = '';
    form.hidden = false;
    $('historical-current-name-input').focus();
  }

  function openCurrentCycleEditor() {
    if (!isAdmin) return;
    $('historical-case-modal-title').textContent = '현재 국면 기준점 수정';
    $('historical-case-code').value = currentModel.code;
    $('historical-case-name').value = currentModel.name;
    $('historical-case-primary-index').value = currentModel.primaryIndex || 'NASDAQ_COMPOSITE';
    $('historical-case-search-start').value = currentModel.searchStart || '';
    $('historical-case-search-end').value = currentModel.searchEnd || '';
    $('historical-case-summary').value = currentModel.summary || '';
    renderCaseCycleRows(currentModel.markets || {});
    $('historical-case-save-status').textContent = '';
    $('historical-case-auto-status').textContent = '';
    $('historical-case-modal').hidden = false;
    $('historical-case-name').focus();
  }

  async function loadManualReasonChoices() {
    if (!functionClient) return;
    try {
      const response = await functionClient.invoke('admin-control', {
        action: 'list_historical_pivot_reasons'
      });
      manualReasonChoices = (response?.reasons || []).filter(item => item?.is_active !== false);
      manualReasonLoadError = '';
      populateManualReasonSelect();
      renderReasonPresetAdmin();
    } catch (error) {
      manualReasonLoadError = '선택 근거 목록을 불러오지 못했습니다.';
      console.error('[Historical manual reason choices]', error);
    }
  }

  function populateManualReasonSelect() {
    const select = $('historical-manual-pivot-reason');
    if (!select) return;
    const current = select.value;
    select.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '선택 근거를 선택하세요';
    select.append(placeholder);
    for (const item of manualReasonChoices) {
      const option = document.createElement('option');
      option.value = item.reason_text;
      option.textContent = item.reason_text;
      select.append(option);
    }
    if (current && [...select.options].some(opt => opt.value === current)) {
      select.value = current;
    }
  }

  function renderReasonPresetAdmin() {
    const list = $('historical-reason-preset-list');
    if (!list || !isAdmin) return;
    list.replaceChildren();
    for (const item of manualReasonChoices) {
      const row = document.createElement('div');
      row.className = 'historical-reason-preset-item';
      const text = document.createElement('span');
      text.textContent = item.reason_text;
      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'historical-reason-preset-del';
      delBtn.title = '프리셋 비활성화';
      delBtn.setAttribute('aria-label', `프리셋 삭제: ${item.reason_text}`);
      delBtn.innerHTML = '<i class="fa-solid fa-trash"></i>';
      delBtn.addEventListener('click', async () => {
        try {
          await functionClient.invoke('admin-control', {
            action: 'delete_historical_pivot_reason',
            reason_id: item.id
          });
          await loadManualReasonChoices();
        } catch (err) {
          alert('삭제 실패: ' + (err?.message || '알 수 없는 오류'));
        }
      });
      row.append(text, delBtn);
      list.append(row);
    }
  }

  function initReasonPresetAccordion() {
    const toggle = $('historical-reason-preset-toggle');
    const content = $('historical-reason-preset-content');
    const addBtn = $('historical-reason-preset-add-btn');
    const input = $('historical-reason-preset-input');
    const status = $('historical-reason-preset-status');

    if (!toggle || !content) return;

    toggle.addEventListener('click', () => {
      const isHidden = content.hidden;
      content.hidden = !isHidden;
      toggle.setAttribute('aria-expanded', String(isHidden));
      const icon = toggle.querySelector('i');
      if (icon) {
        icon.className = `fa-solid ${isHidden ? 'fa-chevron-up' : 'fa-chevron-down'}`;
      }
    });

    if (addBtn && input) {
      addBtn.addEventListener('click', async () => {
        const text = input.value.trim();
        if (!text) {
          if (status) status.textContent = '문구를 입력해 주세요.';
          return;
        }
        addBtn.disabled = true;
        if (status) status.textContent = '추가 중...';
        try {
          await functionClient.invoke('admin-control', {
            action: 'save_historical_pivot_reason',
            reason_text: text
          });
          input.value = '';
          if (status) status.textContent = '추가되었습니다.';
          await loadManualReasonChoices();
        } catch (err) {
          if (status) status.textContent = '추가 실패: ' + (err?.message || '알 수 없는 오류');
        } finally {
          addBtn.disabled = false;
        }
      });
    }
  }

  async function rebuildHistoricalScores(caseCode, indexCode, seriesCodes = null) {
    if (!isAdmin || !functionClient) return;
    await functionClient.invoke('historical-score-rebuild', {
      case_code: caseCode,
      index_code: indexCode,
      series_codes: seriesCodes && seriesCodes.length ? seriesCodes : undefined
    });
  }

  async function topIndicatorTotals(caseCode) {
    const catalog = indicatorRepository.catalog();
    const rows = await window.MacroWatchFrontend.queryAll(client, 'historical_indicator_ai_scores',
      'series_code,by_reference,scoring_version', 'series_code', [['case_code', caseCode]]);
    const bySeries = new Map();
    for (const row of rows) {
      if (row.scoring_version !== indicatorRepository.SCORE_VERSION) continue;
      const list = bySeries.get(row.series_code) || [];
      list.push(row);
      bySeries.set(row.series_code, list);
    }
    return catalog.map(meta => {
      const items = bySeries.get(meta.code) || [];
      if (!items.length) return null;
      let score = 0;
      let hasScore = false;
      for (const row of items) {
        const value = Number(row.by_reference?.LIST?.score);
        if (!Number.isFinite(value)) continue;
        score += value;
        hasScore = true;
      }
      return hasScore ? { meta, score } : null;
    }).filter(Boolean).sort((a, b) => b.score - a.score);
  }

  function renderTopIndicators(items) {
    const panel = $('historical-cycle-top-indicators');
    const list = $('historical-cycle-top-list');
    list.replaceChildren();
    for (const [index, item] of items.slice(0, 3).entries()) {
      const row = document.createElement('li');
      const rank = document.createElement('b');
      const name = document.createElement('span');
      const score = document.createElement('strong');
      rank.textContent = String(index + 1).padStart(2, '0');
      name.textContent = item.meta.title;
      score.textContent = `총 ${item.score}점`;
      row.append(rank, name, score);
      list.append(row);
    }
    panel.hidden = !list.children.length;
  }
  function manualPivotDirectionMatches(pivot, type, pivots, rows, cycle, indexRows = []) {
    if (!pivot.isManual || !['positive', 'inverse'].includes(pivot.relationship) || pivot.keyReference) return true;
    if (pivot.designatedReference && pivot.designatedReference !== type) return false;
    if (!rows?.length) return true;

    const dayDistance = (a, b) => Math.abs(Date.parse(`${a}T00:00:00Z`) - Date.parse(`${b}T00:00:00Z`));
    const owner = pivot.designatedReference
      || [['START', cycle.startDate], ['PEAK', cycle.peakDate], ['TROUGH', cycle.troughDate]]
        .filter(([, date]) => date)
        .sort((a, b) => dayDistance(a[1], pivot.pivotDate) - dayDistance(b[1], pivot.pivotDate))[0]?.[0]
      || type;

    const trough = cycle.troughDate;
    const bufferEnd = trough ? new Date(`${trough}T00:00:00Z`) : null;
    if (bufferEnd) {
      bufferEnd.setUTCDate(1);
      bufferEnd.setUTCMonth(bufferEnd.getUTCMonth() + 24);
      bufferEnd.setUTCDate(Math.min(Number(trough.slice(8, 10)), new Date(Date.UTC(bufferEnd.getUTCFullYear(), bufferEnd.getUTCMonth() + 1, 0)).getUTCDate()));
    }

    const end = owner === 'START' ? cycle.peakDate : owner === 'PEAK' ? cycle.troughDate : bufferEnd?.toISOString().slice(0, 10);
    const nextPivotStart = owner === 'TROUGH' ? (pivot.pivotDate > cycle.troughDate ? pivot.pivotDate : cycle.troughDate) : pivot.pivotDate;
    const next = pivots.find(candidate => candidate.pivotDate > nextPivotStart && candidate.pivotDate <= end);
    const to = next?.pivotDate || (owner === 'TROUGH' ? [...rows].reverse().find(row => row.time <= end)?.time : end);
    if (!to || to <= pivot.pivotDate) return false;

    const fromValue = rawValueAtDate(rows, pivot.pivotDate);
    const toValue = rawValueAtDate(rows, to);
    if (!Number.isFinite(fromValue) || !Number.isFinite(toValue)) return false;

    let direction = Math.sign(toValue - fromValue);
    if (!direction) {
      const previous = [...pivots].reverse().find(candidate => candidate.pivotDate < pivot.pivotDate);
      direction = previous ? Math.sign(fromValue - previous.pivotValue) : 0;
    }
    if (!direction) return true;

    const startIndex = indexRows.find(row => row.time === cycle.troughDate);
    const endIndex = [...indexRows].reverse().find(row => row.time <= to);
    const expected = owner === 'TROUGH' && indexRows.length
      ? startIndex && endIndex && endIndex.time > cycle.troughDate ? Math.sign(endIndex.value - startIndex.value) : 0
      : owner === 'PEAK' ? -1 : 1;
    if (!expected) return false;

    return direction === (pivot.relationship === 'positive' ? expected : -expected);
  }

  function classifyStoredPivots(pivots, cycle, rows = [], indexRows = []) {
    const refs = [['START', cycle?.startDate], ['PEAK', cycle?.peakDate], ['TROUGH', cycle?.troughDate]].filter(([, date]) => date);
    const days = (a, b) => Math.round((Date.parse(`${a}T00:00:00Z`) - Date.parse(`${b}T00:00:00Z`)) / 86400000);
    const source = [...(pivots || [])];
    const selectedByReference = new Map();

    for (const [type, date] of refs) {
      const window = indicatorAnalysis.relevanceWindow(date);
      const candidates = source.filter(pivot => pivot.pivotDate >= window.from && pivot.pivotDate <= window.to
        && pivot.designatedReference !== 'UNCLEAR'
        && (!pivot.designatedReference || pivot.designatedReference === type)
        && manualPivotDirectionMatches(pivot, type, source, rows, cycle, indexRows))
        .sort((a, b) => Number(Boolean(b.isManual)) - Number(Boolean(a.isManual))
          || Math.abs(days(a.pivotDate, date)) - Math.abs(days(b.pivotDate, date))
          || a.pivotDate.localeCompare(b.pivotDate)
          || a.pivotOrder - b.pivotOrder);
      if (candidates.length) selectedByReference.set(type, candidates[0]);
    }

    return source.map(pivot => {
      const selectedRefs = refs.filter(([type]) => selectedByReference.get(type) === pivot)
        .map(([type, date]) => ({ type, date, offsetDays: days(pivot.pivotDate, date) }));
      const extendedRefs = pivot.designatedReference === 'UNCLEAR'
        ? []
        : (pivot.designatedReference ? refs.filter(([type]) => type === pivot.designatedReference) : refs);
      const extended = extendedRefs.map(([type, date]) => ({
        type,
        date,
        window: indicatorAnalysis.nearMissWindow(date),
        offsetDays: days(pivot.pivotDate, date)
      }))
        .filter(item => pivot.pivotDate >= item.window.from && pivot.pivotDate <= item.window.to
          && manualPivotDirectionMatches(pivot, item.type, source, rows, cycle, indexRows))
        .sort((a, b) => Math.abs(a.offsetDays) - Math.abs(b.offsetDays))[0] || null;

      const refDate = pivot.designatedReference && pivot.designatedReference !== 'UNCLEAR' ? cycle?.[`${pivot.designatedReference.toLowerCase()}Date`] : null;
      const designatedPrimary = pivot.designatedReference && refDate ? { type: pivot.designatedReference, date: refDate, offsetDays: days(pivot.pivotDate, refDate) } : null;
      const primary = selectedRefs[0] || extended || designatedPrimary;

      return Object.freeze({
        ...pivot,
        selectedReferences: Object.freeze(selectedRefs),
        referenceType: primary?.type || null,
        referenceDate: primary?.date || null,
        offsetDays: primary?.offsetDays ?? null,
        markerStatus: selectedRefs.length ? 'confirmed' : extended ? 'near_miss' : 'reference_only',
        pivotReason: pivot.selectionReason
      });
    });
  }

  function autoPivotRows(item) {
    return (item.pivots || []).filter(pivot => ['A', 'B', 'C'].includes(String(pivot.grade || '').toUpperCase())).map((pivot, index) => ({
      pivotOrder: index,
      pivotDate: String(pivot.date).slice(0, 10),
      pivotValue: Number(pivot.value),
      pivotType: String(pivot.type || ''),
      selectionReason: String(pivot.reason || '')
    }));
  }

  function mergeManualPivots(item, cycle, indexRows = []) {
    const manual = item.manualPivots || [];
    const blocked = new Set(manual.map(pivot => pivot.sourceDate));
    const active = manual;
    const occupied = new Set(manual.map(pivot => pivot.pivotDate).filter(Boolean));

    const automatic = [...new Map((item.storedPivots?.length ? item.storedPivots : autoPivotRows(item))
      .filter(pivot => !blocked.has(pivot.pivotDate) && !occupied.has(pivot.pivotDate))
      .map(pivot => [pivot.pivotDate, pivot])).values()];

    const merged = [...automatic, ...active.map(pivot => ({
      pivotOrder: Number.MAX_SAFE_INTEGER,
      pivotDate: pivot.pivotDate,
      pivotValue: pivot.pivotValue,
      selectionReason: [pivot.reason, pivot.comment].filter(Boolean).join('\n'),
      pivotReason: [pivot.reason, pivot.comment].filter(Boolean).join('\n'),
      relationship: pivot.relationship,
      sourceDate: pivot.sourceDate,
      keyReference: pivot.keyReference,
      designatedReference: pivot.designatedReference || null,
      keySuppressed: Boolean(pivot.keySuppressed || pivot.designatedReference === 'UNCLEAR'),
      isVerified: Boolean(pivot.isVerified),
      isManual: true
    }))].sort((a, b) => a.pivotDate.localeCompare(b.pivotDate));

    const classified = classifyStoredPivots(merged, cycle, item.rows, indexRows);
    const manualKeys = new Map(active.filter(pivot => pivot.keyReference).map(pivot => [pivot.keyReference, pivot.sourceDate]));
    const referenceDates = { START: cycle?.startDate, PEAK: cycle?.peakDate, TROUGH: cycle?.troughDate };

    return classified.map(pivot => {
      let selectedReferences = (pivot.selectedReferences || []).filter(ref => !pivot.keySuppressed && (!manualKeys.has(ref.type) || pivot.sourceDate === manualKeys.get(ref.type)));
      if (pivot.designatedReference === 'UNCLEAR') {
        selectedReferences = [];
      } else if (pivot.isManual && pivot.keyReference) {
        const date = referenceDates[pivot.keyReference];
        selectedReferences = [{
          type: pivot.keyReference,
          date,
          offsetDays: date ? Math.round((Date.parse(pivot.pivotDate) - Date.parse(date)) / 86400000) : null
        }];
      }
      const overridden = pivot.markerStatus === 'confirmed' && !selectedReferences.length;
      let markerStatus;
      if (pivot.designatedReference === 'UNCLEAR') {
        markerStatus = pivot.isVerified ? 'verified' : 'reference_only';
      } else if (pivot.isManual && pivot.keyReference) {
        markerStatus = 'confirmed';
      } else if (pivot.isManual) {
        if (selectedReferences.length) {
          markerStatus = 'confirmed';
        } else if (pivot.markerStatus !== 'reference_only') {
          markerStatus = 'manual_standard';
        } else if (pivot.isVerified) {
          markerStatus = 'verified';
        } else {
          markerStatus = 'reference_only';
        }
      } else {
        markerStatus = overridden ? 'overridden_key' : selectedReferences.length ? 'confirmed' : pivot.markerStatus;
      }
      return Object.freeze({
        ...pivot,
        selectedReferences: Object.freeze(selectedReferences),
        markerStatus
      });
    });
  }

  function effectivePivots(item, context) {
    let pivots = context.mode === 'history'
      ? (item.manualPivots?.length
        ? mergeManualPivots(item, context.cycle, context.indexRows || [])
        : classifyStoredPivots(autoPivotRows(item), context.cycle, item.rows, context.indexRows || []))
      : [...(item.results || []), ...(item.nearMissPivots || [])];

    if (context.mode === 'current') {
      const latest = item.evidence?.pending
        ? { pivotDate: item.evidence.pending.candidateDate, regimeBoundaryDate: item.evidence.pending.regimeBoundaryDate, referenceType: 'CURRENT_STRUCTURAL', markerStatus: item.evidence.status }
        : item.evidence?.result || null;
      pivots = [...(item.confirmedReferences || []), ...(latest ? [latest] : [])];
    }
    const deduped = [...new Map(pivots.map(pivot => [context.mode === 'history' ? pivot.pivotDate : `${pivot.markerStatus || ''}:${pivot.referenceType || ''}:${pivot.pivotDate}`, pivot])).values()];
    if (context?.displayRange) {
      const { from, to } = context.displayRange;
      return deduped.filter(pivot => (!from || pivot.pivotDate >= from) && (!to || pivot.pivotDate <= to));
    }
    return deduped;
  }

  function displayItem(item, context) {
    return {
      ...item,
      displayRows: indicatorAnalysis.normalizeForDisplay(item.rows, context.displayRange.from, context.displayRange.to),
      displayPivots: effectivePivots(item, context)
    };
  }

  function rawValueAtDate(rows, date) {
    const exact = rows.find(row => row.time === date);
    if (exact) return exact.value;
    const right = rows.findIndex(row => row.time > date);
    const before = rows[right - 1];
    const after = rows[right];
    if (!before || !after) return null;
    const from = Date.parse(before.time);
    const to = Date.parse(after.time);
    const at = Date.parse(date);
    return before.value + (after.value - before.value) * (at - from) / (to - from);
  }

  function closeManualPivotModal() {
    manualPivotContext = null;
    $('historical-manual-pivot-modal').hidden = true;
    $('historical-manual-pivot-status').textContent = '';
  }

  function openManualPivotModal(point) {
    if (!isAdmin || activeMode !== 'history' || !functionClient || !activeIndicatorContext) return;
    const item = activeIndicatorContext.analyses.find(candidate => candidate.meta.code === point.code);
    if (!item) return;

    const targetDate = String(point.date || '').slice(0, 10);
    const manual = (item.manualPivots || []).find(pivot => String(pivot.pivotDate || '').slice(0, 10) === targetDate);
    const automatic = (item.storedPivots?.length ? item.storedPivots : autoPivotRows(item)).find(pivot => String(pivot.pivotDate || '').slice(0, 10) === targetDate);
    const classified = effectivePivots(item, activeIndicatorContext).find(pivot => String(pivot.pivotDate || '').slice(0, 10) === targetDate);

    const designatedReference = manual?.designatedReference || manual?.keyReference || (classified?.designatedReference === 'UNCLEAR' ? 'UNCLEAR' : classified?.referenceType || classified?.selectedReferences?.[0]?.type || '');
    const isKey = (manual?.keySuppressed || designatedReference === 'UNCLEAR') ? false : Boolean(manual?.keyReference || classified?.markerStatus === 'confirmed');
    const isVerified = Boolean(manual?.isVerified || classified?.markerStatus === 'verified');
    const existing = Boolean(manual || automatic);

    manualPivotContext = {
      caseCode: activeCase.code,
      indexCode: activeCode,
      seriesCode: point.code,
      sourceDate: manual?.sourceDate || targetDate,
      item,
      existing,
      keyTouched: false,
      keyDecision: manual?.keyReference ? 'manual_on' : manual?.isVerified ? 'manual_verified' : manual?.designatedReference ? 'manual_ref' : manual?.keySuppressed ? 'manual_off' : 'auto',
      isKey,
      isVerified,
      designatedReference
    };

    $('historical-manual-pivot-title').textContent = `${item.meta.title} · ${existing ? '변곡점 수정' : '변곡점 추가'}`;
    $('historical-manual-pivot-date').value = manual ? manual.pivotDate : point.date;
    $('historical-manual-pivot-relationship').value = manual ? manual.relationship || '' : '';

    const reasonSelect = $('historical-manual-pivot-reason');
    const savedReason = manual ? manual.reason || '' : '';
    reasonSelect.querySelector?.('option[data-legacy-reason]')?.remove();
    if (savedReason && reasonSelect.options && ![...reasonSelect.options].some(option => option.value === savedReason)) {
      const option = new Option(savedReason, savedReason);
      option.dataset.legacyReason = '';
      reasonSelect.add(option);
    }
    reasonSelect.value = savedReason;

    $('historical-manual-pivot-comment').value = manual ? manual.comment || '' : '';
    $('historical-manual-pivot-is-key').checked = isKey;
    $('historical-manual-pivot-is-verified').checked = isVerified;
    $('historical-manual-pivot-reference').value = designatedReference;
    $('historical-manual-pivot-reference').disabled=false;
    $('historical-manual-pivot-delete').hidden = !existing;
    $('historical-manual-pivot-status').textContent = manualReasonLoadError;
    $('historical-manual-pivot-modal').hidden = false;
    $('historical-manual-pivot-date').focus();
  }

  async function persistManualPivot(isDeleted) {
    const context = manualPivotContext;
    if (!isAdmin || !context || !functionClient) return;

    const status = $('historical-manual-pivot-status');
    const date = $('historical-manual-pivot-date').value;
    const isKey = $('historical-manual-pivot-is-key').checked;
    const isVerified = $('historical-manual-pivot-is-verified').checked;
    const selectedRef = $('historical-manual-pivot-reference').value || '';
    const value = isDeleted ? null : rawValueAtDate(context.item.rows, date);

    if (!isDeleted && (!date || !Number.isFinite(value) || (isKey && (!selectedRef || selectedRef === 'UNCLEAR')))) {
      status.textContent = isKey && selectedRef === 'UNCLEAR'
        ? '불명확 기준점은 핵심 변곡점으로 지정할 수 없습니다.'
        : '날짜와 해당 날짜의 지표값을 확인해 주세요. 핵심 변곡점을 선택했다면 지수 기준점(START, PEAK, TROUGH)을 지정해 주세요.';
      return;
    }
    if (!isDeleted && isKey && selectedRef && selectedRef !== 'UNCLEAR' && (context.item.manualPivots || []).some(pivot => pivot.keyReference === selectedRef && pivot.sourceDate !== context.sourceDate)) {
      status.textContent = `이 지표의 ${selectedRef} 핵심 변곡점이 이미 있습니다. 기존 지정을 관리자 화면에서 먼저 해제해 주세요.`;
      return;
    }

    let sendKeyReference;
    if (isDeleted) {
      sendKeyReference = null;
    } else if (selectedRef === 'UNCLEAR') {
      sendKeyReference = isVerified ? 'UNCLEAR_VERIFIED' : 'UNCLEAR';
    } else if (context.keyTouched) {
      sendKeyReference=isKey&&isVerified&&selectedRef?`${selectedRef}_VERIFIED`:isKey?selectedRef:isVerified?'VERIFIED':selectedRef?`${selectedRef}_REF`:null;
    } else if (context.keyDecision === 'manual_off') {
      sendKeyReference = null;
    } else if (context.isKey && context.isVerified && context.designatedReference) {
      sendKeyReference = `${context.designatedReference}_VERIFIED`;
    } else if (context.keyDecision === 'manual_on' || (context.isKey && context.designatedReference)) {
      sendKeyReference = context.designatedReference;
    } else if (context.keyDecision === 'manual_verified' || context.isVerified) {
      sendKeyReference = 'VERIFIED';
    } else if (context.keyDecision === 'manual_ref' || (!context.isKey && context.designatedReference)) {
      sendKeyReference = `${context.designatedReference}_REF`;
    } else {
      sendKeyReference = 'AUTO';
    }

    const save = $('historical-manual-pivot-save');
    const del = $('historical-manual-pivot-delete');
    save.disabled = del.disabled = true;
    status.textContent = '저장 중';

    try {
      await functionClient.invoke('admin-control', {
        action: 'save_historical_indicator_manual_pivot',
        case_code: context.caseCode,
        index_code: context.indexCode,
        series_code: context.seriesCode,
        source_date: context.sourceDate,
        is_deleted: isDeleted,
        pivot_date: isDeleted ? null : date,
        pivot_value: value,
        relationship: isDeleted ? null : $('historical-manual-pivot-relationship').value || null,
        reason: isDeleted ? null : $('historical-manual-pivot-reason').value || null,
        comment: isDeleted ? null : $('historical-manual-pivot-comment').value || null,
        key_reference: sendKeyReference,
        is_verified: isVerified
      });

      indicatorRepository.clearManualPivots(context.caseCode, context.indexCode, context.seriesCode);
      indicatorRepository.clearStoredPivots?.(context.caseCode, context.indexCode, context.seriesCode);
      for (const code of Object.keys(indexData.indices)) {
        indicatorRepository.clearScoreRows(context.caseCode, code);
        indicatorRepository.clearStoredPivots?.(context.caseCode, code, context.seriesCode);
      }
      for (const code of Object.keys(indexData.indices)) {
        analysisCache.delete(`history:${context.caseCode}:${code}`);
      }

      try {
        for (const code of Object.keys(indexData.indices)) {
          await rebuildHistoricalScores(context.caseCode, code, [context.seriesCode]);
        }
      } catch (error) {
        status.textContent = `변곡점은 저장됐지만 점수 갱신 실패: ${error?.message || '알 수 없는 오류'}`;
        return;
      }

      closeManualPivotModal();
      if (activeCase?.code === context.caseCode && activeCode === context.indexCode && activeMode === 'history') {
        await refreshIndicators(requestToken);
      }
    } catch (error) {
      status.textContent = `저장 오류: ${error?.message || '알 수 없는 오류'}`;
    } finally {
      save.disabled = del.disabled = false;
    }
  }
  const referenceOrder=['START', 'PEAK', 'TROUGH'];
  const regimeLabel = type => ({ rising: '상승', falling: '하락', sideways: '횡보' }[type] || type);
  const relationshipLabel = value => ({positive:'정 관계',inverse:'역 관계',unclear:'정/역 관계 불명확'}[value] || value);
  const indicatorValue = (result, meta) => `${window.MacroWatchFrontend.formatDisplayNumber(result.pivotValue, { maximumFractionDigits: meta.decimals })}${meta.unit === '%' ? '%' : ` ${meta.unit}`}`;
  const anchorSummary=item => referenceOrder.filter(type => item.byReference?.[type]);
  const nearMissSummary = item => referenceOrder.filter(type => (item.nearMissPivots || []).some(pivot => pivot.referenceType === type));

  function indicatorBadgeSummary(item, context) {
    if (!item) return { anchors: [], nearMisses: [] };
    if (context.mode !== 'history') {
      const anchors = (item.confirmedReferences || []).map(ref => ref.referenceType).filter(Boolean);
      const nearMisses = (item.nearMissPivots || []).map(ref => ref.referenceType).filter(Boolean);
      return { anchors, nearMisses };
    }
    const pivots = (item.displayPivots || (typeof effectivePivots === 'function' ? effectivePivots(item, context) : []) || []);
    const darkStatuses = new Set(['near_miss', 'overridden_key', 'manual_standard']);
    const anchors = referenceOrder.filter(type => pivots.some(pivot => {
      const references = (pivot.selectedReferences || []).map(ref => ref.type);
      return !darkStatuses.has(pivot.markerStatus) && pivot.markerStatus !== 'reference_only' && pivot.markerStatus !== 'verified' && references.includes(type);
    }));
    const dates = {
      START: context.cycle?.startDate,
      PEAK: context.cycle?.peakDate,
      TROUGH: context.cycle?.troughDate
    };
    const nearMisses = referenceOrder.filter(type => {
      if (!dates[type]) return false;
      const window = indicatorAnalysis.nearMissWindow(dates[type]);
      return pivots.some(pivot => {
        if (!darkStatuses.has(pivot.markerStatus)) return false;
        if (pivot.pivotDate < window.from || pivot.pivotDate > window.to) return false;
        const targetRef = pivot.designatedReference || pivot.referenceType;
        return targetRef === type;
      });
    });
    return { anchors, nearMisses };
  }

  const pivotReasonFor=(item, result) => {
    if (String(result?.pivotReason || '').trim()) {
      return String(result.pivotReason).trim();
    }
    const date = String(result?.pivotDate || '').slice(0, 10);
    const pivot = (item?.pivots || []).find(candidate => String(candidate?.date || '').slice(0, 10) === date);
    return String(pivot?.reason || '').trim();
  };

  function closePivotReviewModal() {
    document.querySelector('.historical-pivot-review-modal')?.remove();
  }

  async function resolveDPivot(item, pivot, resolution, button, status) {
    if (!isAdmin || !functionClient) return;
    button.disabled = true;
    status.textContent = '저장 중';
    try {
      await functionClient.invoke('admin-control', {
        action: 'resolve_historical_pivot_review',
        case_code: activeCase.code,
        index_code: activeCode,
        series_code: item.meta.code,
        pivot_date: pivot.date,
        resolution
      });
      await rebuildHistoricalScores(activeCase.code, activeCode, [item.meta.code]);
      indicatorRepository.clearScoreRows(activeCase.code, activeCode);
      analysisCache.clear();
      closePivotReviewModal();
      await render(activeCode);
    } catch (error) {
      status.textContent = '저장 오류: ' + (error?.message || '알 수 없는 오류');
      button.disabled = false;
    }
  }

  function openPivotReviewModal(item, pivot) {
    if (!isAdmin) return;
    closePivotReviewModal();

    const overlay = document.createElement('div');
    overlay.className = 'historical-pivot-review-modal';
    const card = document.createElement('div');
    card.className = 'historical-pivot-review-card';
    const head = document.createElement('div');
    head.className = 'historical-pivot-review-head';

    const title = document.createElement('strong');
    title.textContent = `D 피봇 검토 · ${item.meta.title}`;
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = '×';
    close.addEventListener('click', closePivotReviewModal);
    head.append(title, close);

    const meta = document.createElement('p');
    meta.className = 'historical-pivot-review-meta';
    meta.textContent = `${pivot.date} · ${pivot.direction || 'neutral'} · ${pivot.type || 'review'}`;

    const reason = document.createElement('p');
    reason.className = 'historical-pivot-review-reason';
    reason.textContent = String(pivot.reason || '').trim() || '판단 근거가 없습니다.';

    const actions = document.createElement('div');
    actions.className = 'historical-pivot-review-actions';
    const status = document.createElement('span');
    status.className = 'historical-pivot-review-status';

    for (const value of ['A', 'B', 'C', 'DELETE']) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.resolution = value;
      button.textContent = value === 'DELETE' ? '삭제' : `${value}로 확정`;
      if (value === 'DELETE') button.className = 'is-delete';
      button.addEventListener('click', () => resolveDPivot(item, pivot, value, button, status));
      actions.append(button);
    }

    card.append(head, meta, reason, actions, status);
    overlay.append(card);
    overlay.addEventListener('click', event => {
      if (event.target === overlay) closePivotReviewModal();
    });
    document.body.append(overlay);
  }

  function appendDReviews(root, item) {
    const pivots = [...(item?.reviewPivots || [])].sort((a, b) => String(a?.date || '').localeCompare(String(b?.date || '')));
    if (!pivots.length) return;

    const section = document.createElement('section');
    section.className = 'historical-d-review-section';
    const title = document.createElement('strong');
    title.className = 'historical-ab-reason-title';
    title.textContent = 'D · 판단 보류';
    const grid = document.createElement('div');
    grid.className = 'historical-d-review-grid';

    for (const pivot of pivots) {
      const card = document.createElement('article');
      const head = document.createElement('strong');
      const body = document.createElement('p');
      head.textContent = `D · ${pivot.date || '날짜 없음'} · ${pivot.direction || 'neutral'}`;
      body.textContent = String(pivot.reason || '').trim() || '근거 미저장';
      card.append(head, body);
      if (isAdmin) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = '판정하기';
        button.addEventListener('click', () => openPivotReviewModal(item, pivot));
        card.append(button);
      }
      grid.append(card);
    }
    section.append(title, grid);
    root.append(section);
  }

  function allIndicatorMetadata() {
    const registry = window.MacroWatchEconomicSeriesRegistry?.allSeries || [];
    return registry.filter(item => !['SP500', 'NASDAQ_COMPOSITE', 'KOSPI'].includes(item.code));
  }

  function closeHiddenIndicatorMenu() {
    const menu = $('historical-indicator-hidden-menu');
    if (menu) menu.hidden = true;
  }

  function renderHiddenIndicatorMenu() {
    const menu = $('historical-indicator-hidden-menu');
    if (!menu || !isAdmin) return;
    menu.replaceChildren();

    const byCode = new Map(allIndicatorMetadata().map(item => [item.code, item]));
    const items = [...hiddenIndicatorCodes]
      .map(code => byCode.get(code))
      .filter(Boolean)
      .sort((a, b) => a.title.localeCompare(b.title, 'ko'));

    if (!items.length) {
      const p = document.createElement('p');
      p.className = 'historical-indicator-empty';
      p.textContent = '삭제한 지표가 없습니다.';
      menu.append(p);
      return;
    }

    for (const item of items) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = item.title;
      const icon = document.createElement('i');
      icon.className = 'fa-solid fa-plus';
      button.append(icon);
      button.addEventListener('click', () => restoreIndicator(item.code));
      menu.append(button);
    }
  }

  async function setIndicatorHidden(code, hidden) {
    if (!isAdmin || !indicatorRepository || !currentUser) return;
    await indicatorRepository.setHidden(code, hidden, currentUser.id);
    hiddenIndicatorCodes = new Set(await indicatorRepository.loadVisibility());
    analysisCache.clear();
    indicatorRepository.clearAnalysisData();
    if (hidden && selection?.snapshot().selected === code) selection.clear();
    closeHiddenIndicatorMenu();
    if (activeCase) await render(activeCode);
  }

  async function deleteIndicator(code) {
    if (!isAdmin) return;
    try {
      await setIndicatorHidden(code, true);
    } catch (error) {
      console.error('[Historical indicator delete]', error);
      state('error', '지표 삭제 설정을 저장하지 못했습니다.');
    }
  }

  async function restoreIndicator(code) {
    if (!isAdmin) return;
    try {
      await setIndicatorHidden(code, false);
    } catch (error) {
      console.error('[Historical indicator restore]', error);
      state('error', '지표 복구 설정을 저장하지 못했습니다.');
    }
  }

  function renderIndicators(context) {
    activeIndicatorContext = context;
    const group = candidatesFor(context);
    visibleIndicators = group.items;
    const snapshot = selection.reconcile(visibleIndicators);
    const root = $('historical-indicator-list');
    root.replaceChildren();

    if (!visibleIndicators.length) {
      const p = document.createElement('p');
      p.className = 'historical-indicator-empty';
      p.textContent = activeMode === 'history'
        ? '이 국면에 표시할 지표 데이터가 없습니다.'
        : '과거 국면에서 의미 있었던 지표가 없습니다.';
      root.append(p);
    } else {
      if (activeMode === 'current') {
        const p = document.createElement('p');
        p.className = 'historical-indicator-note';
        p.textContent = '과거 국면에서 한 번이라도 의미 있었던 전체 지표를 매일 같은 기준으로 스캔합니다.';
        root.append(p);
      }
      for (const item of visibleIndicators) {
        const row = document.createElement('div');
        const label = document.createElement('label');
        const input = document.createElement('input');
        const name = document.createElement('span');
        const { anchors, nearMisses } = indicatorBadgeSummary(item, context);
        const signalState = item.evidence?.signalState;

        row.className = 'historical-indicator-row';
        input.type='radio';
        input.name='historical-indicator';
        input.value = item.meta.code;
        input.checked = snapshot.selected === item.meta.code;
        input.addEventListener('change', () => {
          if (!input.checked) return;
          selection.select(item.meta.code);drawIndicators(context);focusCase();
        });

        name.textContent = item.meta.title;
        label.append(input, name);

        if (activeMode === 'history') {
          if (item.reviewPivots?.length) {
            const review = document.createElement('small');
            review.textContent = `D검토 ${item.reviewPivots.length}`;
            label.append(review);
          }
          const score = document.createElement('em');
          score.className = 'historical-indicator-score';
          score.textContent = `${item.byReference?.LIST?.score ?? 0}점`;
          label.append(score);
        } else {
          const score = document.createElement('em');
          score.className = 'historical-indicator-score';
          score.textContent = signalState === 'market_relevant_confirmed'
            ? `시장 관련 ${Math.round(item.evidence.score)}점`
            : signalState === 'structural_only'
              ? `구조 유지 ${Math.round(item.evidence.score)}점`
              : signalState === 'candidate'
                ? `후보 ${Math.round(item.evidence.score)}점`
                : signalState === 'watch'
                  ? `감시 ${Math.round(item.evidence.score)}점`
                  : '신호 없음';
          label.append(score);
        }

        if (anchors.length || nearMisses.length) {
          const badges = document.createElement('span');
          label.className = 'has-references';
          badges.className = 'historical-indicator-badges';
          for (const type of anchors) {
            const badge = document.createElement('b');
            badge.className='historical-reference-badge';
            badge.dataset.reference = type;
            badge.textContent = type;
            badges.append(badge);
          }
          for (const type of nearMisses) {
            const badge = document.createElement('b');
            badge.className = 'historical-reference-badge is-near-miss';
            badge.textContent = type;
            badges.append(badge);
          }
          label.append(badges);
        }
        row.append(label);

        if (isAdmin) {
          const actions = document.createElement('span');
          const del = document.createElement('button');
          actions.className = 'historical-indicator-actions';
          del.type = 'button';
          del.className = 'is-delete';
          del.title = '전체 국면에서 지표 삭제';
          del.setAttribute('aria-label', item.meta.title + ' 삭제');
          del.innerHTML = '<i class="fa-solid fa-trash"></i>';
          del.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            deleteIndicator(item.meta.code);
          });
          actions.append(del);
          row.append(actions);
        }
        root.append(row);
      }
    }

    const signal = $('historical-cycle-signal');
    const assessment = group.signal;
    signal.hidden = activeMode !== 'current' || !assessment?.probability;
    if (!signal.hidden) {
      $('historical-cycle-signal-title').textContent = `주가 피봇 가능성 ${assessment.probability}%`;
      $('historical-cycle-signal-text').textContent = `시장 관련 확정 ${assessment.marketRelevantCount}개 · 구조 신호 ${assessment.structuralOnlyCount}개 · 후보 ${assessment.candidateCount}개 · 감시 ${assessment.watchCount}개를 품질·지속성·시너지와 무효화에 따른 점수 회수까지 반영해 계산했습니다.`;
    }
    drawIndicators(context);
  }

  function drawIndicators(context) {
    const selectedCode = selection.snapshot().selected;
    const item = visibleIndicators.find(candidate => candidate.meta.code === selectedCode);
    const selected = item ? [displayItem(item, context)] : [];
    chart?.setIndicators?.(selected);
    chart?.setIndicatorPointClick?.(isAdmin && activeMode === 'history' ? openManualPivotModal : null);
    $('historical-indicator-clear').disabled = !selected.length;

    const legend = $('historical-indicator-legend');
    legend.replaceChildren();
    legend.hidden = !selected.length;
    if (selected.length) {
      const label = document.createElement('span');
      const colors = chart?.indicatorColors?.() || new Map();
      const { anchors } = indicatorBadgeSummary(selected[0], context);
      label.style?.setProperty('--indicator-color', colors.get(selectedCode));
      label.textContent = `${selected[0].meta.title}${anchors.length ? ` · ${anchors.join('/')}` : ''}`;
      legend.append(label);
    }

    if (context.mode === 'history') {
      renderHistoricalPivotScores(selected[0], context);
    } else {
      renderDetail(selected[0]);
    }
  }

  function renderHistoricalPivotScores(item, context) {
    const root = $('historical-indicator-detail');
    if (!item) {
      root.hidden = true;
      root.replaceChildren();
      return;
    }
    root.hidden = false;
    root.replaceChildren();

    const primary = document.createElement('div');
    const heading = document.createElement('div');
    const title = document.createElement('strong');
    const metaLine = document.createElement('span');
    const grid = document.createElement('div');

    primary.className = 'historical-indicator-primary';
    heading.className = 'historical-indicator-detail-heading';
    title.textContent = item.meta.title;
    metaLine.textContent = `${item.meta.category} · ${item.meta.frequencyLabel} · ${item.meta.unit} · 관측일 원자료 기준`;
    heading.append(title, metaLine);
    primary.append(heading);

    grid.className = 'historical-indicator-result-grid historical-pivot-detail-grid';
    const dates = {
      START: context.cycle.startDate,
      PEAK: context.cycle.peakDate,
      TROUGH: context.cycle.troughDate
    };

    function appendCard(target, type, score, isDark) {
      const result = score;
      const card = document.createElement('article');
      const label = document.createElement('strong');
      const body = document.createElement('p');
      const hasPivot = Boolean(score?.pivotDate);
      const offset = score?.offsetDays;

      card.classList.toggle('is-empty',!hasPivot);
      card.classList.toggle('is-dark', Boolean(isDark));
      label.textContent = `${type} · ${hasPivot ? (offset === 0 ? '기준점 당일' : `기준점 ${Math.abs(offset)}일 ${offset < 0 ? '전' : '후'}`) : '기준점 피봇 없음'} · ${relationshipLabel(score?.relationship || 'unclear')} · 종합 점수 ${score?.score ?? 0}점`;
      body.textContent = hasPivot
        ? `기준점 ${dates[type]}\n피봇점 ${score.pivotDate} · 수치 ${indicatorValue(score, item.meta)}\n변곡 시의성 ${score.timelinessScore}점 · 관계 적합성 ${score.relationshipSuitabilityScore}점 · 추세 지속성 ${score.continuityScore}점`
        : `기준점 ${dates[type] || '미지정'}\n반영된 변곡점 없음 · 종합 점수 0점\n기준점 -3개월 ~ +1개월 범위에 유효한 피봇이 없습니다.`;
      card.append(label, body);

      const reason = pivotReasonFor(item, result);
      if (reason) {
        const box = document.createElement('div');
        const reasonLabel = document.createElement('b');
        const reasonText = document.createElement('p');
        box.className = 'historical-pivot-reason';
        reasonLabel.textContent = '피봇 판정 근거';
        reasonText.textContent = reason;
        box.append(reasonLabel, reasonText);
        card.append(box);
      }
      target.append(card);
    }

    function appendVerifiedCard(target, pivot) {
      const card = document.createElement('article');
      const label = document.createElement('strong');
      const body = document.createElement('p');
      card.classList.add('is-verified');

      const refType = pivot.designatedReference;
      const refDate = refType && dates[refType] ? dates[refType] : null;
      const offset = refDate ? Math.round((Date.parse(pivot.pivotDate) - Date.parse(refDate)) / 86400000) : null;
      const timingText = offset !== null ? (offset === 0 ? '기준점 당일' : `기준점 ${Math.abs(offset)}일 ${offset < 0 ? '전' : '후'}`) : '';

      label.textContent = refType
        ? `${refType} 확인 · ${timingText} · ${relationshipLabel(pivot.relationship || 'unclear')}`
        : `확인 변곡점 · ${pivot.pivotDate} · ${relationshipLabel(pivot.relationship || 'unclear')}`;
      body.textContent = refDate
        ? `기준점 ${refDate}\n피봇점 ${pivot.pivotDate} · 수치 ${indicatorValue(pivot, item.meta)}`
        : `피봇점 ${pivot.pivotDate} · 수치 ${indicatorValue(pivot, item.meta)}`;
      card.append(label, body);

      const reason = pivotReasonFor(item, pivot) || pivot.pivotReason || pivot.selectionReason;
      if (reason) {
        const box = document.createElement('div');
        const reasonLabel = document.createElement('b');
        const reasonText = document.createElement('p');
        box.className = 'historical-pivot-reason';
        reasonLabel.textContent = '피봇 판정 근거';
        reasonText.textContent = reason;
        box.append(reasonLabel, reasonText);
        card.append(box);
      }
      target.append(card);
    }

    referenceOrder.forEach(type => appendCard(grid, type, item.byReference?.[type]?.markerStatus === 'confirmed' ? item.byReference[type] : null, false));
    primary.append(grid);root.append(primary);

    const darkPivots = item.byReference?.LIST?.darkPivots || [];
    if (darkPivots.length) {
      const darkHeading = document.createElement('strong');
      const darkGrid = document.createElement('div');
      darkHeading.className='historical-pivot-dark-heading';darkHeading.textContent='준핵심 변곡점';
      const darkDesc = document.createElement('p');
      darkDesc.className='historical-pivot-dark-desc';
      darkDesc.textContent = '지수 기준점과 타이밍은 다소 차이가 있으나, 시장의 방향성을 조기에 예고했거나 사후에 추세를 확증해 준 의미 있는 변곡점입니다.';
      darkGrid.className = 'historical-indicator-result-grid historical-pivot-detail-grid';
      darkPivots.forEach(score => appendCard(darkGrid, score.referenceType, score, true));
      root.append(darkHeading,darkDesc,darkGrid);
    }

    const verifiedPivots = (item.displayPivots || (typeof effectivePivots === 'function' ? effectivePivots(item, context) : []) || []).filter(pivot => pivot.markerStatus === 'verified');
    if (verifiedPivots.length) {
      const verifiedHeading = document.createElement('strong');
      const verifiedDesc = document.createElement('p');
      const verifiedGrid = document.createElement('div');
      verifiedHeading.className = 'historical-pivot-verified-heading';
      verifiedHeading.textContent = '확인 변곡점';
      verifiedDesc.className = 'historical-pivot-verified-desc';
      verifiedDesc.textContent = '관리자가 시장 흐름 분석을 위해 확인용으로 별도 지정한 의미 있는 변곡점입니다.';
      verifiedGrid.className = 'historical-indicator-result-grid historical-pivot-detail-grid';
      verifiedPivots.forEach(pivot => appendVerifiedCard(verifiedGrid, pivot));
      root.append(verifiedHeading, verifiedDesc, verifiedGrid);
    }

    appendDReviews(root, item);
  }

  function renderDetail(item) {
    const root = $('historical-indicator-detail');
    if (!item) {
      root.hidden = true;
      root.replaceChildren();
      return;
    }
    root.hidden = false;
    root.replaceChildren();

    const primary = document.createElement('div');
    const heading = document.createElement('div');
    const title = document.createElement('strong');
    const metaLine = document.createElement('span');
    const grid = document.createElement('div');

    primary.className = 'historical-indicator-primary';
    heading.className = 'historical-indicator-detail-heading';
    title.textContent = item.meta.title;
    metaLine.textContent = `${item.meta.category} · ${item.meta.frequencyLabel} · ${item.meta.unit} · 관측일 원자료 기준`;
    heading.append(title, metaLine);
    primary.append(heading);

    grid.className = 'historical-indicator-result-grid';
    for (const [key, label] of [['structural', '구조 품질'], ['timing', '시의성'], ['duration', '추세 지속성'], ['overall', '종합 판정']]) {
      const card = document.createElement('article');
      const name = document.createElement('strong');
      const value = document.createElement('p');
      name.textContent = label;
      value.textContent = key === 'overall' ? `${Math.round(item.evidence?.score ?? item.overallScore)}점` : `${Math.round(item.scores?.[key] ?? 0)}점`;
      card.append(name, value);
      grid.append(card);
    }
    primary.append(grid);
    root.append(primary);

    appendEvidenceDetails(root, item.evidence);
  }

  function appendEvidenceDetails(root, evidence) {
    if (!evidence) return;
    if (evidence.pending) {
      const note = document.createElement('p');
      note.className = 'historical-indicator-note';
      note.textContent = `진행 중인 후보: ${evidence.pending.candidateDate} 기준 ${regimeLabel(evidence.pending.regime)} 구간 · ${evidence.pending.daysSinceCandidate}일 경과 · 최소 확인 기준 ${evidence.pending.requiredDays}일`;
      root.append(note);
    }
    if (evidence.marketRelevant?.confirmedAnchors?.length) {
      const confirmed = evidence.marketRelevant.confirmedAnchors.map(anchor => `${anchor.referenceType} (${anchor.indicatorDate})`);
      const note = document.createElement('p');
      note.className = 'historical-indicator-note';
      note.textContent = `시장 기준점 관련 확정: ${confirmed.join(' · ')} · 각 기준점 -3개월~+1개월 안의 구조 피봇만 반영`;
      root.append(note);
    }
    if (evidence.invalidations?.length) {
      const latestInvalidation = evidence.invalidations.at(-1);
      const note = document.createElement('p');
      note.className = 'historical-indicator-note';
      note.textContent = `최근 무효화/교체: ${latestInvalidation.invalidationDate} · ${latestInvalidation.reason}`;
      root.append(note);
    }
  }

  async function refreshIndicators(token) {
    if (!indicatorRepository || !selection) return;
    indicatorLoading();
    try {
      const context = await calculateIndicatorContext();
      if (token !== requestToken) return;
      renderIndicators(context);
      if (context.mode === 'history') {
        try {
          const totals = await topIndicatorTotals(activeCase.code);
          if (token === requestToken) renderTopIndicators(totals);
        } catch (error) {
          if (token === requestToken) console.error('[Historical top indicators]', error);
        }
      }
    } catch (error) {
      if (token !== requestToken) return;
      indicatorLoading('비교 지표를 불러오지 못했습니다.');
      console.error('[Historical indicators]', error);
    }
  }

  async function render(code) {
    const token = ++requestToken;
    setMarket(code);
    $('historical-cycle-top-indicators').hidden = true;
    meta.textContent = `${activeCase?.name || 'Historical Case'} · ${indexData?.indices[code] || code}`;
    state('loading', '사이클 데이터 불러오는 중');
    chart?.setIndicators?.([]);

    try {
      if (!indexData || !cycleData || !window.MacroWatchHistoricalChart || !window.MacroWatchFrontend) {
        throw new Error('화면 모듈을 불러오지 못했습니다.');
      }
      if (!chart) chart = window.MacroWatchHistoricalChart.create(host);
      chart.setData([]);
      const rows = await indexRepository.load(code);
      if (token !== requestToken) return;

      const activeCycle = cycleData.marketCycle(activeCase, code);
      const metrics = cycleData.calculate(activeCase, activeCycle, rows);
      activeRows = rows;
      chart.setData(rows);
      if (!rows.length) {
        state('empty', '저장된 지수 데이터가 없습니다.');
        return;
      }

      chart.setCycle(cycleData.chartPoints(activeCycle, rows));
      focusCase();
      showCycle(activeCase, activeCycle, metrics);
      const count = window.MacroWatchFrontend.formatDisplayNumber(rows.length, { locale: 'ko-KR' });
      state('ready', `${count}개 · ${rows[0].time} ~ ${rows.at(-1).time}`);
      await refreshIndicators(token);if(token===requestToken)focusCase();
    } catch (error) {
      if (token !== requestToken) return;
      chart?.destroy();
      chart = null;
      activeRows = [];
      state('error', '사이클 데이터를 불러오지 못했습니다. 다시 시도해 주세요.');
      console.error('[Historical Insight]', error);
    }
  }

  function selectCase(code) {
    const item = code === currentModel?.code ? currentModel : cases.find(candidate => candidate.code === code);
    if (!item) return;
    const caseChanged=Boolean(activeCase&&activeCase.code!==item.code);if(caseChanged){clearIndicatorSelection();activeIndicatorContext=null;visibleIndicators=[];}
    if (isHistoricalCase(item)) {
      activeHistoricalCode = item.code;
      if (caseChanged || expandedHistoricalCode === null) expandedHistoricalCode = item.code;
    }
    activeCase = item;
    activeCaseButton();
    return render(item.primaryIndex);
  }

  function updateModeAvailability() {
    const hasHistorical = cases.some(isHistoricalCase);
    for (const button of modeButtons) {
      button.disabled = button.dataset.historicalMode === 'history' && !hasHistorical;
    }
  }

  function closeCurrentNameEditor() {
    const form = $('historical-current-name-form');
    form.hidden = true;
    $('historical-current-name-status').textContent = '';
  }

  async function saveCurrentName(event) {
    event.preventDefault();
    if (!isAdmin || !functionClient) return;
    const input = $('historical-current-name-input');
    const status = $('historical-current-name-status');
    const save = $('historical-current-name-save');
    const name = input.value.trim();
    if (!name) {
      status.textContent = '국면 이름을 입력해 주세요.';
      return;
    }

    save.disabled = true;
    status.textContent = '저장 중';
    try {
      await functionClient.invoke('admin-control', {
        action: 'save_historical_current_name',
        name
      });
      currentModel.name = name;
      showCycle(currentModel, cycleData.marketCycle(currentModel, activeCode), cycleData.calculate(currentModel, cycleData.marketCycle(currentModel, activeCode), activeRows));
      renderCases();
      closeCurrentNameEditor();
    } catch (error) {
      status.textContent = '저장 오류: ' + (error?.message || '알 수 없는 오류');
      console.error('[Historical current name save]', error);
    } finally {
      save.disabled = false;
    }
  }

  function candidatesFor(context) {
    const query = $('historical-indicator-search').value.trim().toLowerCase();
    const items = (context.analyses || []).filter(item => {
      if (hiddenIndicatorCodes.has(item.meta.code)) return false;
      if (!query) return true;
      return item.meta.title.toLowerCase().includes(query) || item.meta.category.toLowerCase().includes(query);
    });
    return {
      items,
      signal: context.signal
    };
  }

  async function calculateIndicatorContext() {
    const cacheKey = `${activeMode}:${activeCase.code}:${activeCode}`;
    if (analysisCache.has(cacheKey)) return analysisCache.get(cacheKey);

    const catalog = indicatorRepository.catalog();
    const cycle = cycleData.marketCycle(activeCase, activeCode);
    const scoreRows = activeMode === 'history' ? await indicatorRepository.loadScoreRows(activeCase.code, activeCode) : new Map();
    const manualPivotsBySeries = new Map();
    const storedPivotsBySeries = new Map();
    if (activeMode === 'history') {
      await Promise.all(catalog.map(async meta => {
        const [manual, stored] = await Promise.all([
          indicatorRepository.loadManualPivots(activeCase.code, activeCode, meta.code),
          indicatorRepository.loadStoredPivots(activeCase.code, activeCode, meta.code)
        ]);
        manualPivotsBySeries.set(meta.code, manual);
        storedPivotsBySeries.set(meta.code, stored);
      }));
    }
    const analyses = [];

    for (const meta of catalog) {
      const rows = await indicatorRepository.load(meta.code);
      if (!rows.length) continue;
      const cachedScore = scoreRows.get(meta.code);
      const manualPivots = manualPivotsBySeries.get(meta.code) || [];
      const storedPivots = storedPivotsBySeries.get(meta.code) || [];
      const item = activeMode === 'history'
        ? indicatorAnalysis.historicalAnalysisWithStoredScores(
          meta,
          rows,
          activeCase,
          cycle,
          cachedScore,
          activeRows,
          manualPivots,
          storedPivots,
          indicatorRepository.SCORE_VERSION
        )
        : indicatorAnalysis.currentAnalysis(meta, rows, currentModel, activeRows);
      if (item) analyses.push(item);
    }

    const latestDate = activeRows.at(-1)?.time || activeCase.searchEnd;
    const context = {
      mode: activeMode,
      cycle,
      displayRange: indicatorAnalysis.displayWindow(activeCase, cycle, latestDate),
      analyses: indicatorAnalysis.sortAnalyses(analyses, activeMode),
      signal: activeMode === 'current' ? indicatorAnalysis.currentAssessment(analyses) : null,
      indexRows: activeRows
    };

    analysisCache.set(cacheKey, context);
    return context;
  }

  function clearIndicatorSelection() {
    selection?.clear();
    chart?.setIndicators?.([]);
    $('historical-indicator-clear').disabled = true;
    $('historical-indicator-legend').hidden = true;
    $('historical-indicator-legend').replaceChildren();
    $('historical-indicator-detail').hidden = true;
    $('historical-indicator-detail').replaceChildren();
  }

  function bindEvents() {
    $('historical-case-search-start')?.addEventListener('change', () => {
      const start = $('historical-case-search-start').value;
      const end = $('historical-case-search-end').value;
      if (start && end) calculateAutoCycles();
    });
    $('historical-case-search-end')?.addEventListener('change', () => {
      const start = $('historical-case-search-start').value;
      const end = $('historical-case-search-end').value;
      if (start && end) calculateAutoCycles();
    });
    $('historical-case-primary-index')?.addEventListener('change', () => {
      const start = $('historical-case-search-start').value;
      const end = $('historical-case-search-end').value;
      if (start && end) calculateAutoCycles();
    });

    $('historical-manual-pivot-is-key')?.addEventListener('change', event => {
      if (!manualPivotContext) return;
      manualPivotContext.keyTouched = true;
      const isKey = event.target.checked;
      const refSelect = $('historical-manual-pivot-reference');
      if (isKey) {
        if (!refSelect.value || refSelect.value === 'UNCLEAR') {
          refSelect.value = manualPivotContext.designatedReference && manualPivotContext.designatedReference !== 'UNCLEAR' ? manualPivotContext.designatedReference : 'START';
        }
      }
    });

    $('historical-manual-pivot-is-verified')?.addEventListener('change', () => {
      if (!manualPivotContext) return;
      manualPivotContext.keyTouched = true;
    });

    $('historical-manual-pivot-reference')?.addEventListener('change', event => {
      if (!manualPivotContext) return;
      manualPivotContext.keyTouched = true;
      manualPivotContext.designatedReference = event.target.value || null;
      if (event.target.value === 'UNCLEAR') {
        const keyCheckbox = $('historical-manual-pivot-is-key');
        if (keyCheckbox) keyCheckbox.checked = false;
      }
    });

    for (const button of modeButtons) {
      button.addEventListener('click', async () => {
        const mode = button.dataset.historicalMode;
        if (!mode || mode === activeMode) return;
        setMode(mode);
        renderCases();
        const targetCode = mode === 'current'
          ? currentModel.code
          : activeHistoricalCode || cases.find(isHistoricalCase)?.code;
        if (targetCode) await selectCase(targetCode);
      });
    }

    for (const button of marketButtons) {
      button.addEventListener('click', async () => {
        const code = button.dataset.historicalIndex;
        if (!code || code === activeCode) return;
        await render(code);
      });
    }

    $('historical-indicator-search').addEventListener('input', () => {
      if (!activeIndicatorContext) return;
      renderIndicators(activeIndicatorContext);
    });

    $('historical-indicator-clear').addEventListener('click', () => {
      clearIndicatorSelection();
      if (!activeIndicatorContext) return;
      renderIndicators(activeIndicatorContext);
    });

    $('historical-focus-case').addEventListener('click', () => focusCase());
    $('historical-case-add').addEventListener('click', () => openCaseModal());
    $('historical-case-modal-close').addEventListener('click', closeCaseModal);
    $('historical-case-cancel').addEventListener('click', closeCaseModal);
    $('historical-case-form').addEventListener('submit', saveCase);
    $('historical-case-auto-calc').addEventListener('click', calculateAutoCycles);

    $('historical-current-cycle-edit').addEventListener('click', openCurrentCycleEditor);
    $('historical-current-name-edit').addEventListener('click', openCurrentNameEditor);
    $('historical-current-name-cancel').addEventListener('click', closeCurrentNameEditor);
    $('historical-current-name-form').addEventListener('submit', saveCurrentName);

    $('historical-manual-pivot-modal-close')?.addEventListener('click', closeManualPivotModal);
    $('historical-manual-pivot-cancel')?.addEventListener('click', closeManualPivotModal);
    $('historical-manual-pivot-save')?.addEventListener('click', () => persistManualPivot(false));
    $('historical-manual-pivot-delete')?.addEventListener('click', () => persistManualPivot(true));

    initReasonPresetAccordion();

    const hiddenBtn = $('historical-indicator-hidden-toggle');
    if (hiddenBtn) {
      hiddenBtn.addEventListener('click', event => {
        event.stopPropagation();
        const menu = $('historical-indicator-hidden-menu');
        if (menu.hidden) {
          renderHiddenIndicatorMenu();
          menu.hidden = false;
        } else {
          menu.hidden = true;
        }
      });
    }
    document.addEventListener('click', event => {
      const menu = $('historical-indicator-hidden-menu');
      if (menu && !menu.hidden && !menu.contains(event.target) && event.target !== $('historical-indicator-hidden-toggle')) {
        menu.hidden = true;
      }
    });
  }

  async function init() {
    bindEvents();
    state('loading', '인증 상태 확인 중');

    try {
      const core = window.MacroWatchFrontend;
      if (!core) throw new Error('공통 모듈을 불러오지 못했습니다.');
      client = core.getClient();
      functionClient = core.getFunctionClient();
      currentUser = await core.getCurrentUser();
      isAdmin = await core.isAdminUser();

      setMode('history');
      if (!isAdmin) {
        $('historical-case-add').hidden = true;
        $('historical-current-name-edit').hidden = true;
        $('historical-current-cycle-edit').hidden = true;
        $('historical-indicator-hidden-toggle').hidden = true;
        $('historical-indicator-hidden-menu').hidden = true;
      }

      indexRepository = indexData.createRepository(client);
      indicatorRepository = window.MacroWatchHistoricalIndicators.createRepository(client);
      if (isAdmin) {
        hiddenIndicatorCodes = new Set(await indicatorRepository.loadVisibility());
        await loadManualReasonChoices();
      }

      const repository = cycleData.createRepository(client);
      const loadedCases = await repository.loadCases();
      cases = loadedCases.filter(isHistoricalCase);
      currentModel = loadedCases.find(item => item.code === 'current_cycle') || cycleData.defaultCurrentModel();

      updateModeAvailability();
      renderCases();
      activeHistoricalCode = cases[0]?.code || null;
      expandedHistoricalCode = cases[0]?.code || null;
      renderCases();
      const initial = cases[0] || currentModel;
      if (initial) await selectCase(initial.code);
    } catch (error) {
      state('error', '초기화 실패: ' + (error?.message || '알 수 없는 오류'));
      console.error('[Historical Insight init]', error);
    }
  }

  window.addEventListener('DOMContentLoaded', init);
})();
