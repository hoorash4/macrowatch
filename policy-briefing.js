(() => {
  'use strict';

  const PAGE_SIZE = 5;
  const state = { rows: [], page: 0, openMeetingDate: null };

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);

  function meetingTitleParts(row) {
    const [year, month, day] = String(row.meeting_date).split('-').map(Number);
    return {
      date: `${year}년 ${month}월 ${day}일`,
      label: `${row.is_emergency ? '긴급' : '정례'} FOMC 회의 결과`,
    };
  }

  function rateDecision(row) {
    const lower = Number(row.target_range_lower);
    const upper = Number(row.target_range_upper);
    const hasLower = Number.isFinite(lower);
    const hasUpper = Number.isFinite(upper);
    const range = hasLower && hasUpper
      ? (lower === upper ? `${lower.toFixed(2)}%` : `${lower.toFixed(2)}~${upper.toFixed(2)}%`)
      : '금리 수준 미확인';
    const action = ({ hike: '인상', cut: '인하', hold: '동결' })[row.action] || '결정 미확인';
    const change = row.action !== 'hold' && Number.isFinite(Number(row.change_bps))
      ? ` · ${Math.abs(Number(row.change_bps))}bp ${action}`
      : ` · ${action}`;
    return `기준금리 ${range}${change}`;
  }

  function textSection(title, text, icon, className = '') {
    if (!text) return '';
    return `<section class="fomc-briefing-section ${className}"><h4><span><i class="fa-solid ${icon}" aria-hidden="true"></i></span>${escapeHtml(title)}</h4><div class="fomc-briefing-copy">${escapeHtml(text)}</div></section>`;
  }

  function changesSection(changes) {
    if (!Array.isArray(changes) || !changes.length) return '';
    return `<section class="fomc-briefing-section"><h4><span><i class="fa-solid fa-code-compare" aria-hidden="true"></i></span>이전 성명서와 달라진 점</h4><div class="fomc-briefing-changes">${changes.map((change) => `
      <article>
        <strong>${escapeHtml(change.title)}</strong>
        <p>${escapeHtml(change.explanation)}</p>
        ${change.previous_expression || change.current_expression ? `<div><span>이전</span>${escapeHtml(change.previous_expression || '—')}<span>현재</span>${escapeHtml(change.current_expression || '—')}</div>` : ''}
      </article>`).join('')}</div></section>`;
  }

  function detailContent(row) {
    const briefing = row.briefing || {};
    return `<div class="fomc-briefing-detail-scroll">
      <section class="fomc-briefing-result">
        <h3><span><i class="fa-solid fa-landmark" aria-hidden="true"></i></span>${escapeHtml(row.is_emergency ? '긴급 FOMC 결과' : '정례 FOMC 결과')}</h3>
        <p>${escapeHtml(rateDecision(row))}</p>
      </section>
      ${textSection('FOMC 성명서 브리핑', briefing.statement_briefing, 'fa-file-lines', 'fomc-briefing-section--lead')}
      <div class="fomc-briefing-context-grid">
        ${textSection('경기', briefing.economy, 'fa-chart-line')}
        ${textSection('물가', briefing.inflation, 'fa-gauge-high')}
        ${textSection('고용', briefing.employment, 'fa-user-group')}
        ${textSection('기타', briefing.other, 'fa-ellipsis')}
      </div>
      ${textSection('금리 결정의 핵심 이유', briefing.key_rate_reason, 'fa-bullseye', 'fomc-briefing-section--reason')}
      ${changesSection(briefing.changes_from_previous)}
      ${textSection('AI 종합 분석', briefing.ai_overall_analysis, 'fa-brain', 'fomc-briefing-section--analysis')}
      <div class="fomc-briefing-close-row"><button type="button" data-fomc-close="${escapeHtml(row.meeting_date)}"><i class="fa-solid fa-chevron-up" aria-hidden="true"></i> 브리핑 닫기</button></div>
    </div>`;
  }

  function render() {
    const container = document.getElementById('fomc-briefing-list');
    if (!container) return;
    if (!state.rows.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">저장된 FOMC 브리핑이 아직 없습니다.</div>';
      return;
    }
    const pageCount = Math.ceil(state.rows.length / PAGE_SIZE);
    state.page = Math.min(state.page, pageCount - 1);
    const pageRows = state.rows.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
    container.innerHTML = `<div class="fomc-briefing-items">${pageRows.map((row) => {
      const isOpen = state.openMeetingDate === row.meeting_date;
      const panelId = `fomc-briefing-${row.meeting_date}`;
      const title = meetingTitleParts(row);
      return `<article class="fomc-briefing-item${isOpen ? ' is-open' : ''}">
        <button type="button" class="fomc-briefing-toggle" data-fomc-meeting-date="${escapeHtml(row.meeting_date)}" aria-expanded="${isOpen}" aria-controls="${panelId}">
          <span class="fomc-briefing-title-wrap"><span class="fomc-briefing-title-mark"><i class="fa-solid fa-calendar-day" aria-hidden="true"></i></span><span class="fomc-briefing-title-text"><strong><span class="fomc-briefing-title-date">${escapeHtml(title.date)}</span><span class="fomc-briefing-title-label">${escapeHtml(title.label)}</span></strong><small>${escapeHtml(rateDecision(row))}</small></span></span>
          <i class="fa-solid fa-chevron-down" aria-hidden="true"></i>
        </button>
        <div id="${panelId}" class="fomc-briefing-detail"${isOpen ? '' : ' hidden'}>${isOpen ? detailContent(row) : ''}</div>
      </article>`;
    }).join('')}</div>
    <nav class="fomc-briefing-pagination" aria-label="FOMC 브리핑 페이지">
      <button type="button" data-fomc-page="previous" ${state.page === 0 ? 'disabled' : ''}><i class="fa-solid fa-chevron-left" aria-hidden="true"></i> 이전</button>
      <span>${state.page + 1} / ${pageCount}</span>
      <button type="button" data-fomc-page="next" ${state.page >= pageCount - 1 ? 'disabled' : ''}>다음 <i class="fa-solid fa-chevron-right" aria-hidden="true"></i></button>
    </nav>`;
  }

  function bindInteractions() {
    const container = document.getElementById('fomc-briefing-list');
    if (!container || container.dataset.bound === 'true') return;
    container.dataset.bound = 'true';
    container.addEventListener('click', (event) => {
      const closeButton = event.target.closest('[data-fomc-close]');
      if (closeButton) {
        const meetingDate = closeButton.dataset.fomcClose;
        state.openMeetingDate = null;
        render();
        window.requestAnimationFrame(() => document.querySelector(`[data-fomc-meeting-date="${meetingDate}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
        return;
      }
      const toggle = event.target.closest('[data-fomc-meeting-date]');
      if (toggle) {
        const meetingDate = toggle.dataset.fomcMeetingDate;
        state.openMeetingDate = state.openMeetingDate === meetingDate ? null : meetingDate;
        render();
        if (state.openMeetingDate) window.requestAnimationFrame(() => document.querySelector(`[data-fomc-meeting-date="${meetingDate}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
        return;
      }
      const pager = event.target.closest('[data-fomc-page]');
      if (!pager || pager.disabled) return;
      state.page += pager.dataset.fomcPage === 'next' ? 1 : -1;
      state.openMeetingDate = null;
      render();
    });
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('fomc-briefing-list');
    if (!container || !supabaseClient) return;
    bindInteractions();
    const { data, error } = await supabaseClient.from('central_bank_policy_events')
      .select('meeting_date,is_emergency,action,target_range_lower,target_range_upper,change_bps,briefing,briefing_revision')
      .eq('central_bank', 'fed').eq('analysis_status', 'completed').not('briefing', 'is', null)
      .order('meeting_date', { ascending: false });
    if (error) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-44 items-center justify-center rounded-xl border border-dashed border-slate-700 bg-slate-950/30 p-5 text-sm text-slate-500">FOMC 브리핑을 불러오지 못했습니다.</div>';
      return;
    }
    state.rows = data || [];
    state.page = 0;
    state.openMeetingDate = null;
    render();
  }

  window.MacroWatchDashboard?.registerLoader(load);
})();
