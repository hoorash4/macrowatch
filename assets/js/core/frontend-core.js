(() => {
  'use strict';

  // 모든 브라우저 화면이 공유하는 작은 기반 모듈입니다.
  // 화면별 상태나 렌더링은 넣지 않고, 공개 설정·Supabase 연결·안전한 문자열
  // 표시처럼 페이지에 관계없이 항상 같은 동작만 관리합니다.
  const { supabaseUrl, supabasePublishableKey } = window.MACROWATCH_CONFIG;

  function createSupabaseClient() {
    return window.supabase?.createClient(supabaseUrl, supabasePublishableKey) || null;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"]/g, (character) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
    })[character]);
  }

  function createFunctionClient(supabaseClient) {
    // 인증이 필요한 Edge Function 호출의 토큰·갱신·오류 해석을 통일합니다.
    // 각 화면은 함수 이름과 payload만 제공하고 세션 처리 방식을 따로 만들지 않습니다.
    async function accessToken() {
      if (!supabaseClient) throw new Error('Supabase 연결 정보를 확인해 주세요.');
      const { data, error } = await supabaseClient.auth.getSession();
      const token = data?.session?.access_token;
      if (error || !token) throw new Error('로그인이 필요합니다.');
      return token;
    }

    async function invoke(functionName, payload, options = {}, retried = false) {
      const requiresAuth = options.authenticated !== false;
      const response = await fetch(`${supabaseUrl}/functions/v1/${functionName}`, {
        method: 'POST',
        headers: {
          apikey: supabasePublishableKey,
          Authorization: `Bearer ${requiresAuth ? await accessToken() : supabasePublishableKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (requiresAuth && response.status === 401 && !retried) {
        const refreshed = await supabaseClient.auth.refreshSession();
        if (!refreshed.error && refreshed.data.session) {
          return invoke(functionName, payload, options, true);
        }
      }
      if (!response.ok) {
        const fallback = typeof options.errorMessage === 'function'
          ? options.errorMessage(response.status)
          : options.errorMessage || `${functionName} 요청에 실패했습니다. (${response.status})`;
        throw new Error(data?.error || fallback);
      }
      if (data?.error) throw new Error(data.error);
      return data;
    }

    return Object.freeze({ invoke });
  }

  function installConfiguredWorkspaceLinks() {
    if (typeof document === 'undefined') return;
    const links = Array.isArray(window.MACROWATCH_CONFIG?.workspaceLinks)
      ? window.MACROWATCH_CONFIG.workspaceLinks
      : [];
    const actions = document.querySelector('.dashboard-nav-actions');
    if (!actions || !links.length) return;
    links.forEach((item) => {
      if (!item?.href || actions.querySelector(`[data-workspace-link="${item.href}"]`)) return;
      const link = document.createElement('a');
      link.className = 'dashboard-nav-item';
      link.dataset.workspaceLink = item.href;
      link.href = item.href;
      link.target = item.target || '_self';
      if (link.target === '_blank') link.rel = 'noopener noreferrer';
      link.innerHTML = `${item.iconClass ? `<i class="fa-solid ${escapeHtml(item.iconClass)}"></i>` : ''}<span>${escapeHtml(item.label || item.href)}</span>`;
      actions.before(link);
    });
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', installConfiguredWorkspaceLinks);
  }

  window.MacroWatchFrontend = Object.freeze({
    config: Object.freeze({ supabaseUrl, supabasePublishableKey }),
    createFunctionClient,
    createSupabaseClient,
    escapeHtml,
  });
})();
