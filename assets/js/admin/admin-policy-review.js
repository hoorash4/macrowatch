(() => {
  'use strict';

  const { formatDisplayNumber, escapeHtml } = window.MacroWatchFrontend;

  const REASON_OPTIONS = [
    ['inflation_fight', '물가 억제'],
    ['growth_overheat', '경기·금융시장 과열'],
    ['recession_financial_stress', '경기침체·금융시스템 위험'],
    ['insurance_easing', '보험성·선제적 완화'],
    ['normalization_hike', '정상화 인상'],
    ['normalization_cut', '정상화 인하'],
    ['uncertain', '불명확'],
  ];

  const ACTION_LABELS = {
    hike: '인상',
    hold: '동결',
    cut: '인하'
  };

  const REVIEW_LABELS = {
    selected: '그래프에서 선택',
    latest: '최근 FOMC 결과',
    uncertain: '이유 불명확'
  };

  const storageKey = 'macrowatch_policy_review_dates';
  const urlMeetingDate = new URLSearchParams(window.location.search).get('policy_date') || '';

  let requestedMeetingDates = [];
  try {
    requestedMeetingDates = JSON.parse(window.localStorage.getItem(storageKey) || '[]');
  } catch (_) {
    requestedMeetingDates = [];
  }
  requestedMeetingDates = [
    ...new Set([
      ...(Array.isArray(requestedMeetingDates) ? requestedMeetingDates : []),
      ...(urlMeetingDate ? [urlMeetingDate] : [])
    ])
  ];

  function actionLabel(action) {
    return ACTION_LABELS[action] || action;
  }

  function reviewLabel(reviewType) {
    return REVIEW_LABELS[reviewType] || '정책 배경 변경';
  }

  function refreshAttentionCount() {
    const list = document.getElementById('policy-review-list');
    if (!list) return;
    const count = [...list.querySelectorAll('[data-policy-review]')]
      .filter((item) => item.dataset.reviewType !== 'latest').length;
    window.MacroWatchAdminApi?.setListAttentionCount('policy-review-list', count);
  }

  function renderPolicyReviewArticle(item) {
    const options = REASON_OPTIONS.map(([value, label]) => (
      `<option value="${value}" ${item.primary_reason === value ? 'selected' : ''}>${label}</option>`
    )).join('');

    const currentReviewLabel = reviewLabel(item.review_type);
    const bpsText = Number.isFinite(Number(item.change_bps))
      ? ` · ${formatDisplayNumber(Math.abs(Number(item.change_bps)))}bp`
      : '';
    const roundedScore = Math.round(Number(item.score) || 0);

    return (
      `<article class="border-b border-slate-800 p-4 last:border-0" data-policy-review="${escapeHtml(item.meeting_date)}" data-policy-action="${escapeHtml(item.action)}" data-review-type="${escapeHtml(item.review_type)}">` +
      `<div class="mb-3 flex flex-wrap items-center justify-between gap-2">` +
      `<div>` +
      `<strong class="text-sm text-slate-100">${escapeHtml(item.meeting_date)}</strong>` +
      `<span class="ml-2 text-xs font-semibold text-sky-300">${escapeHtml(actionLabel(item.action))}${bpsText}</span>` +
      `</div>` +
      `<span class="rounded-full border border-amber-700/50 bg-amber-950/40 px-2 py-0.5 text-[11px] text-amber-300">${currentReviewLabel}</span>` +
      `</div>` +
      `<form autocomplete="off" class="policy-review-form grid grid-cols-1 gap-2 md:grid-cols-[1fr_1fr_7rem_auto]">` +
      `<select name="primary_reason" autocomplete="off" required class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white outline-none focus:border-blue-500">` +
      `<option value="">정규 이유 선택</option>` +
      `${options}` +
      `</select>` +
      `<input name="reason_keyword" autocomplete="off" maxlength="80" value="${escapeHtml(item.reason_keyword)}" placeholder="이유 키워드 (선택)" class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white outline-none focus:border-blue-500">` +
      `<input name="score" autocomplete="off" required type="number" step="1" min="-1000" max="1000" value="${escapeHtml(roundedScore)}" placeholder="점수 (0도 직접 입력)" class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white outline-none focus:border-blue-500">` +
      `<button type="submit" class="rounded-lg bg-blue-600 px-3 py-2 text-xs font-bold text-white hover:bg-blue-500 disabled:opacity-60">확정</button>` +
      `</form>` +
      `</article>`
    );
  }

  function render(items) {
    const list = document.getElementById('policy-review-list');
    if (!list) return;

    // 최신 회의는 상시 수정용이므로 실제 검토 대기 건수에서는 제외합니다.
    const attentionCount = items.filter((item) => item.review_type !== 'latest').length;
    window.MacroWatchAdminApi?.setListAttentionCount('policy-review-list', attentionCount);

    if (!items.length) {
      list.innerHTML = '<p class="p-4 text-center text-sm text-slate-500">검토할 FOMC 정책 판단이 없습니다.</p>';
      return;
    }

    list.innerHTML = items.map(renderPolicyReviewArticle).join('');
    list.querySelectorAll('.policy-review-form').forEach((form) => {
      form.addEventListener('submit', resolve);
    });
  }

  async function load() {
    const api = window.MacroWatchAdminApi;
    const list = document.getElementById('policy-review-list');
    if (!api || !list) return;

    list.innerHTML = '<p class="p-4 text-center text-sm text-slate-500">검토 목록을 불러오는 중입니다.</p>';
    try {
      const payload = requestedMeetingDates.length ? { meeting_dates: requestedMeetingDates } : {};
      const result = await api.invoke('list_policy_reviews', payload);
      render(result.items || []);
    } catch (error) {
      api.setListAttentionCount('policy-review-list', 0);
      list.innerHTML = `<p class="p-4 text-center text-sm text-red-300">${escapeHtml(error.message || '검토 목록을 불러오지 못했습니다.')}</p>`;
    }
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

      requestedMeetingDates = requestedMeetingDates.filter((date) => date !== article.dataset.policyReview);
      window.localStorage.setItem(storageKey, JSON.stringify(requestedMeetingDates));
      article.remove();

      const list = document.getElementById('policy-review-list');
      if (list && !list.querySelector('[data-policy-review]')) {
        list.innerHTML = '<p class="p-4 text-center text-sm text-slate-500">검토할 FOMC 정책 판단이 없습니다.</p>';
      }

      refreshAttentionCount();
      window.MacroWatchAdminApi.notice('정책 판단 확정', '관리자 이유와 점수를 저장하고 정책 이력을 다시 계산했습니다.');
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
