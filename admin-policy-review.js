(() => {
  'use strict';

  const REASON_OPTIONS = [
    ['inflation_fight', '물가 억제'],
    ['growth_overheat', '경기·금융시장 과열'],
    ['recession_financial_stress', '경기침체·금융시스템 위험'],
    ['insurance_easing', '보험성·선제적 완화'],
    ['uncertain', '불명확'],
  ];

  const escapeHtml = (value) => String(value || '').replace(/[&<>"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[character]);
  const actionLabel = (action) => ({ hike: '인상', hold: '동결', cut: '인하' })[action] || action;

  function render(items) {
    const list = document.getElementById('policy-review-list');
    if (!list) return;
    if (!items.length) {
      list.innerHTML = '<p class="p-4 text-center text-sm text-slate-500">검토할 FOMC 정책 판단이 없습니다.</p>';
      return;
    }
    const options = REASON_OPTIONS.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
    list.innerHTML = items.map((item) => `<article class="border-b border-slate-800 p-4 last:border-0" data-policy-review="${escapeHtml(item.meeting_date)}"><div class="mb-3 flex flex-wrap items-center justify-between gap-2"><div><strong class="text-sm text-slate-100">${escapeHtml(item.meeting_date)}</strong><span class="ml-2 text-xs font-semibold text-sky-300">${escapeHtml(actionLabel(item.action))}${Number.isFinite(Number(item.change_bps)) ? ` · ${Math.abs(Number(item.change_bps))}bp` : ''}</span></div><span class="rounded-full border border-amber-700/50 bg-amber-950/40 px-2 py-0.5 text-[11px] text-amber-300">${item.review_type === 'uncertain' ? '이유 불명확' : '정책 배경 변경'}</span></div><form class="policy-review-form grid grid-cols-1 gap-2 md:grid-cols-[1fr_1fr_7rem_auto]"><select name="primary_reason" required class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white outline-none focus:border-blue-500"><option value="">정규 이유 선택</option>${options}</select><input name="reason_keyword" maxlength="80" placeholder="이유 키워드 (불명확은 선택)" class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white outline-none focus:border-blue-500"><input name="score" required type="number" step="0.001" min="-1000" max="1000" placeholder="점수 (0도 직접 입력)" class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white outline-none focus:border-blue-500"><button type="submit" class="rounded-lg bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-500 disabled:opacity-60">확정</button></form></article>`).join('');
    list.querySelectorAll('.policy-review-form').forEach((form) => form.addEventListener('submit', resolve));
  }

  async function load() {
    const api = window.MacroWatchAdminApi;
    const list = document.getElementById('policy-review-list');
    if (!api || !list) return;
    list.innerHTML = '<p class="p-4 text-center text-sm text-slate-500">검토 목록을 불러오는 중입니다.</p>';
    try { render((await api.invoke('list_policy_reviews')).items || []); }
    catch (error) { list.innerHTML = `<p class="p-4 text-center text-sm text-red-300">${escapeHtml(error.message || '검토 목록을 불러오지 못했습니다.')}</p>`; }
  }

  async function resolve(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const article = form.closest('[data-policy-review]');
    const submit = form.querySelector('button[type="submit"]');
    const values = new FormData(form);
    submit.disabled = true;
    try {
      await window.MacroWatchAdminApi.invoke('resolve_policy_review', {
        meeting_date: article.dataset.policyReview,
        primary_reason: values.get('primary_reason'),
        reason_keyword: values.get('reason_keyword'),
        score: Number(values.get('score')),
      });
      window.MacroWatchAdminApi.notice('정책 판단 확정', '관리자 이유와 점수를 저장하고 정책 이력을 다시 계산했습니다.');
      await load();
    } catch (error) {
      window.MacroWatchAdminApi.notice('정책 판단 저장 실패', error.message || '저장하지 못했습니다.', true);
      submit.disabled = false;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-policy-review-button')?.addEventListener('click', load);
  });
  window.addEventListener('macrowatch:admin-ready', load);
})();
