(() => {
  'use strict';

  // 모든 브라우저 화면이 공유하는 작은 기반 모듈입니다.
  // 화면별 상태나 렌더링은 넣지 않고, 공개 설정·Supabase 연결·안전한 문자열
  // 표시처럼 페이지에 관계없이 항상 같은 동작만 관리합니다.
  const { supabaseUrl, supabasePublishableKey } = window.MACROWATCH_CONFIG;
  const THEME_CACHE_KEY = 'macrowatch.theme-preference';
  const THEME_VALUES = new Set(['system', 'light', 'dark']);
  const darkMedia = window.matchMedia?.('(prefers-color-scheme: dark)') || null;
  let themePreference = 'system';
  let themeClient = null;
  let themeAuthSubscription = null;
  const lightweightCharts = new Set();

  function normalizeThemePreference(value) {
    return THEME_VALUES.has(value) ? value : 'system';
  }

  function resolveTheme(preference, systemDark = Boolean(darkMedia?.matches)) {
    const normalized = normalizeThemePreference(preference);
    return normalized === 'system' ? (systemDark ? 'dark' : 'light') : normalized;
  }

  function cachedThemePreference() {
    try {
      return normalizeThemePreference(window.localStorage.getItem(THEME_CACHE_KEY));
    } catch {
      return 'system';
    }
  }

  function cacheThemePreference(preference) {
    try {
      window.localStorage.setItem(THEME_CACHE_KEY, normalizeThemePreference(preference));
    } catch {
      // 브라우저 저장소를 사용할 수 없어도 현재 탭의 테마는 정상 동작합니다.
    }
  }

  function themeCss() {
    return `
:root {
  color-scheme: light;
  --theme-page-bg: #e3e9ed;
  --theme-surface: #f7f5f0;
  --theme-surface-card: rgba(246,250,252,.68);
  --theme-surface-elevated: #ffffff;
  --theme-surface-muted: #e9eef1;
  --theme-text: #161616;
  --theme-text-secondary: #64748b;
  --theme-text-muted: #82909c;
  --theme-border: #dedede;
  --theme-divider: #e2e8f0;
  --theme-hover: #eef2f7;
  --theme-selected: #111827;
  --theme-input-bg: #ffffff;
  --theme-input-border: #cbd5e1;
  --theme-shadow: 0 18px 45px rgba(47,66,80,.08);
  --theme-chart-bg: #ffffff;
  --theme-chart-grid: #e5e7eb;
  --theme-chart-text: #6b7280;
  --theme-chart-border: #d1d5db;
  --theme-chart-crosshair: #9ca3af;
  --theme-tooltip-bg: #ffffff;
  --theme-tooltip-text: #111827;
}
html[data-theme="dark"] {
  color-scheme: dark;
  --theme-page-bg: #090f1a;
  --theme-surface: #0f172a;
  --theme-surface-card: rgba(15,23,42,.88);
  --theme-surface-elevated: #111c2f;
  --theme-surface-muted: #172033;
  --theme-text: #e7edf5;
  --theme-text-secondary: #a9b5c5;
  --theme-text-muted: #8291a5;
  --theme-border: #334155;
  --theme-divider: #263449;
  --theme-hover: #17243a;
  --theme-selected: #e7edf5;
  --theme-input-bg: #0b1322;
  --theme-input-border: #3b4b61;
  --theme-shadow: 0 18px 45px rgba(0,0,0,.28);
  --theme-chart-bg: #0b1322;
  --theme-chart-grid: #243247;
  --theme-chart-text: #9eacbf;
  --theme-chart-border: #3a4a60;
  --theme-chart-crosshair: #708197;
  --theme-tooltip-bg: #111c2f;
  --theme-tooltip-text: #e7edf5;
  --color-surface-page: #090f1a;
  --color-surface-card: rgba(15,23,42,.88);
  --color-surface-paper: #0f172a;
  --color-surface-soft: #111c2f;
  --color-surface-muted: #172033;
  --color-text-primary: #e7edf5;
  --color-text-dashboard: #e7edf5;
  --color-text-secondary: #a9b5c5;
  --color-text-muted: #8291a5;
  --color-text-description: #9eacbf;
  --color-border-light: #334155;
  --color-dark-panel: #0f172a;
  --color-dark-deep: #090f1a;
  --color-dark-border: #334155;
  --color-dark-input-border: #3b4b61;
  --color-chart-axis: #9eacbf;
  --shadow-card: 0 18px 45px rgba(0,0,0,.28);
  --tracker-card-background: rgba(15,23,42,.9);
  --analysis-card-header-border: rgba(148,163,184,.18);
  --analysis-card-description-color: #9eacbf;
  --analysis-chart-region-background: #0f172a;
  --analysis-chart-region-border: #334155;
  --analysis-chart-plot-background: #0b1322;
  --tracker-drop-indicator-color: #cbd5e1;
}
html[data-theme="dark"] body { background: var(--theme-page-bg) !important; color: var(--theme-text); }
html[data-theme="dark"] .dashboard-workspace,
html[data-theme="dark"] .dashboard-main,
html[data-theme="dark"] .dashboard-panels { color: var(--theme-text); }
html[data-theme="dark"] .analysis-chart-shell,
html[data-theme="dark"] .analysis-chart-frame,
html[data-theme="dark"] .analysis-chart-region,
html[data-theme="dark"] .analysis-chart-plot { background-color: var(--theme-chart-bg); }
html[data-theme="dark"] .analysis-chart-shell svg text { fill: var(--theme-chart-text) !important; }
html[data-theme="dark"] .analysis-chart-axis-line { stroke: var(--theme-chart-border) !important; }
html[data-theme="dark"] .analysis-chart-shell [class*="grid"] line,
html[data-theme="dark"] .analysis-chart-shell line[class*="grid"] { stroke: var(--theme-chart-grid) !important; }
html[data-theme="dark"] .modal-overlay section,
html[data-theme="dark"] .profile-dialog { background-color: var(--theme-surface) !important; border-color: var(--theme-border) !important; color: var(--theme-text); }
html[data-theme="dark"] input,
html[data-theme="dark"] select,
html[data-theme="dark"] textarea { background-color: var(--theme-input-bg); border-color: var(--theme-input-border); color: var(--theme-text); }
html[data-theme="dark"] input::placeholder,
html[data-theme="dark"] textarea::placeholder { color: var(--theme-text-muted); }
html[data-theme="dark"] .bg-white { background-color: var(--theme-surface-elevated) !important; }
html[data-theme="dark"] .bg-slate-50,
html[data-theme="dark"] .bg-slate-100 { background-color: var(--theme-surface-muted) !important; }
html[data-theme="dark"] .text-slate-900,
html[data-theme="dark"] .text-slate-800 { color: var(--theme-text) !important; }
html[data-theme="dark"] .text-slate-700,
html[data-theme="dark"] .text-slate-600 { color: var(--theme-text-secondary) !important; }
html[data-theme="dark"] .border-slate-200,
html[data-theme="dark"] .border-slate-300 { border-color: var(--theme-border) !important; }
html[data-theme="dark"] #auth-screen .auth-canvas { background: var(--theme-page-bg); }
html[data-theme="dark"] #auth-screen .auth-header,
html[data-theme="dark"] #auth-screen .auth-panel,
html[data-theme="dark"] .auth-header .evotive-source-logo { background-color: var(--theme-surface); color: var(--theme-text); }
html[data-theme="dark"] #auth-screen .auth-header { border-bottom-color: var(--theme-border); }
html[data-theme="dark"] #auth-screen .auth-panel .text-white,
html[data-theme="dark"] .auth-header .font-cinzel { color: var(--theme-text); }
html[data-theme="dark"] #auth-screen .auth-panel input,
html[data-theme="dark"] #auth-screen .auth-panel select { background: var(--theme-input-bg); border-color: var(--theme-input-border); color: var(--theme-text); }
html[data-theme="dark"] .auth-divider-line { background: var(--theme-border); }
html[data-theme="dark"] #password-login-button { background: #1d4ed8; border-color: #2563eb; color: #fff; box-shadow: none; }
html[data-theme="dark"] #password-login-button:hover { background: #2563eb; }
html[data-theme="dark"] .economic-chart-page { background: var(--theme-page-bg); color: var(--theme-text); }
html[data-theme="dark"] .economic-topbar { background: rgba(15,23,42,.96); border-color: var(--theme-border); }
html[data-theme="dark"] .economic-brand { color: var(--theme-text); }
html[data-theme="dark"] .economic-brand small,
html[data-theme="dark"] .economic-nav a,
html[data-theme="dark"] .economic-hero p,
html[data-theme="dark"] .economic-status,
html[data-theme="dark"] .economic-chart-meta,
html[data-theme="dark"] .economic-legend { color: var(--theme-text-secondary); }
html[data-theme="dark"] .economic-nav a:hover { background: var(--theme-hover); color: var(--theme-text); }
html[data-theme="dark"] .economic-nav a.is-active,
html[data-theme="dark"] .economic-series-button.is-active,
html[data-theme="dark"] .economic-tools button.is-active,
html[data-theme="dark"] .economic-modal-actions .primary { background: #e5e7eb; border-color: #e5e7eb; color: #111827; }
html[data-theme="dark"] .economic-workspace,
html[data-theme="dark"] .economic-modal-card { background: var(--theme-surface); border-color: var(--theme-border); box-shadow: var(--theme-shadow); }
html[data-theme="dark"] .economic-series-panel { background: #0b1322; border-color: var(--theme-divider); }
html[data-theme="dark"] .economic-series-button { color: var(--theme-text-secondary); }
html[data-theme="dark"] .economic-series-button:hover,
html[data-theme="dark"] .economic-tools button:hover,
html[data-theme="dark"] .economic-series-restore:hover { background: var(--theme-hover); color: var(--theme-text); }
html[data-theme="dark"] .economic-series-restore,
html[data-theme="dark"] .economic-tools button,
html[data-theme="dark"] .economic-modal-actions button { background: var(--theme-surface-elevated); border-color: var(--theme-border); color: var(--theme-text-secondary); }
html[data-theme="dark"] .economic-chart-header,
html[data-theme="dark"] .economic-chart-footer { border-color: var(--theme-divider); }
html[data-theme="dark"] .economic-modal-card label { color: var(--theme-text-secondary); }
html[data-theme="dark"] .economic-modal-card select,
html[data-theme="dark"] .economic-modal-card input { background: var(--theme-input-bg); border-color: var(--theme-input-border); color: var(--theme-text); }
html[data-theme="dark"] .economic-modal-head small,
html[data-theme="dark"] .economic-modal-help { color: var(--theme-text-secondary); }
html[data-theme="dark"] .economic-modal-actions .danger { background: var(--theme-surface-elevated); }
.theme-preference-card { margin-top: 1rem; border: 1px solid var(--theme-border); border-radius: .75rem; background: color-mix(in srgb, var(--theme-surface-elevated) 60%, transparent); padding: 1rem; }
.theme-preference-card label { display: block; color: var(--theme-text-secondary); font-size: .75rem; font-weight: 700; }
.theme-preference-card select { width: 100%; margin-top: .55rem; border: 1px solid var(--theme-input-border); border-radius: .5rem; background: var(--theme-input-bg); color: var(--theme-text); padding: .65rem .75rem; font-size: .875rem; outline: none; }
.theme-preference-card select:focus { border-color: #3b82f6; }
.theme-preference-card p { margin: .45rem 0 0; color: var(--theme-text-muted); font-size: .7rem; line-height: 1.45; }
`;
  }

  function installThemeStyles() {
    if (document.getElementById('macrowatch-theme-styles')) return;
    const style = document.createElement('style');
    style.id = 'macrowatch-theme-styles';
    style.textContent = themeCss();
    document.head.append(style);
  }

  function lightweightThemeOptions() {
    const dark = document.documentElement.dataset.theme === 'dark';
    const root = getComputedStyle(document.documentElement);
    const variable = (name, fallback) => root.getPropertyValue(name).trim() || fallback;
    return {
      layout: {
        background: { color: variable('--theme-chart-bg', dark ? '#0b1322' : '#fff') },
        textColor: variable('--theme-chart-text', dark ? '#9eacbf' : '#6b7280'),
      },
      grid: {
        vertLines: { color: variable('--theme-chart-grid', dark ? '#243247' : '#e5e7eb') },
        horzLines: { color: variable('--theme-chart-grid', dark ? '#243247' : '#e5e7eb') },
      },
      rightPriceScale: { borderColor: variable('--theme-chart-border', dark ? '#3a4a60' : '#d1d5db') },
      timeScale: { borderColor: variable('--theme-chart-border', dark ? '#3a4a60' : '#d1d5db') },
      crosshair: {
        vertLine: { color: variable('--theme-chart-crosshair', dark ? '#708197' : '#9ca3af') },
        horzLine: { color: variable('--theme-chart-crosshair', dark ? '#708197' : '#9ca3af') },
      },
    };
  }

  function refreshLightweightCharts() {
    const options = lightweightThemeOptions();
    lightweightCharts.forEach((chart) => {
      try { chart.applyOptions(options); } catch { /* chart may already be removed */ }
    });
  }

  function installLightweightChartsAdapter() {
    const library = window.LightweightCharts;
    if (!library?.createChart || library.createChart.__macroWatchThemeAware) return;
    const original = library.createChart.bind(library);
    const wrapped = (container, options = {}) => {
      const themed = lightweightThemeOptions();
      const merged = {
        ...options,
        layout: { ...(options.layout || {}), ...themed.layout },
        grid: {
          ...(options.grid || {}),
          vertLines: { ...(options.grid?.vertLines || {}), ...themed.grid.vertLines },
          horzLines: { ...(options.grid?.horzLines || {}), ...themed.grid.horzLines },
        },
        rightPriceScale: { ...(options.rightPriceScale || {}), ...themed.rightPriceScale },
        timeScale: { ...(options.timeScale || {}), ...themed.timeScale },
        crosshair: {
          ...(options.crosshair || {}),
          vertLine: { ...(options.crosshair?.vertLine || {}), ...themed.crosshair.vertLine },
          horzLine: { ...(options.crosshair?.horzLine || {}), ...themed.crosshair.horzLine },
        },
      };
      const chart = original(container, merged);
      lightweightCharts.add(chart);
      const remove = chart.remove?.bind(chart);
      if (remove) chart.remove = () => { lightweightCharts.delete(chart); remove(); };
      return chart;
    };
    wrapped.__macroWatchThemeAware = true;
    library.createChart = wrapped;
  }

  function applyThemePreference(preference, { cache = true, announce = true } = {}) {
    themePreference = normalizeThemePreference(preference);
    const effective = resolveTheme(themePreference);
    document.documentElement.dataset.themePreference = themePreference;
    document.documentElement.dataset.theme = effective;
    if (cache) cacheThemePreference(themePreference);
    const select = document.getElementById('theme-preference');
    if (select && select.value !== themePreference) select.value = themePreference;
    refreshLightweightCharts();
    if (announce) {
      window.dispatchEvent(new CustomEvent('macrowatch:themechange', { detail: { preference: themePreference, theme: effective } }));
    }
    return effective;
  }

  async function themeSupabaseClient() {
    if (window.macroWatchSupabase) return window.macroWatchSupabase;
    if (!themeClient) themeClient = createSupabaseClient();
    return themeClient;
  }

  async function loadStoredThemePreference() {
    const client = await themeSupabaseClient();
    if (!client) return themePreference;
    const { data: sessionData } = await client.auth.getSession();
    const userId = sessionData?.session?.user?.id;
    if (!userId) return themePreference;
    const { data, error } = await client.from('user_accounts')
      .select('theme_preference')
      .eq('user_id', userId)
      .maybeSingle();
    if (error) {
      console.warn('theme preference load failed', error);
      return themePreference;
    }
    applyThemePreference(data?.theme_preference || 'system');
    return themePreference;
  }

  async function saveThemePreference(preference) {
    const normalized = normalizeThemePreference(preference);
    const previous = themePreference;
    applyThemePreference(normalized);
    const client = await themeSupabaseClient();
    if (!client) return normalized;
    const { data: sessionData } = await client.auth.getSession();
    if (!sessionData?.session?.user?.id) return normalized;
    const { error } = await client.rpc('set_theme_preference', { p_theme: normalized });
    if (error) {
      applyThemePreference(previous);
      throw error;
    }
    return normalized;
  }

  function installThemePreferenceControl() {
    if (document.getElementById('theme-preference')) return;
    const body = document.querySelector('#profile-modal .profile-dialog-body');
    if (!body) return;
    const card = document.createElement('section');
    card.className = 'theme-preference-card';
    card.innerHTML = `
      <label for="theme-preference">화면 테마</label>
      <select id="theme-preference" aria-label="화면 테마">
        <option value="system">시스템 설정</option>
        <option value="light">라이트 모드</option>
        <option value="dark">다크 모드</option>
      </select>
      <p>시스템 설정은 기기의 라이트/다크 모드 변경을 실시간으로 따릅니다.</p>`;
    body.prepend(card);
    const select = card.querySelector('#theme-preference');
    select.value = themePreference;
    select.addEventListener('change', async () => {
      select.disabled = true;
      try {
        await saveThemePreference(select.value);
      } catch (error) {
        window.alert(error?.message || '테마 설정을 저장하지 못했습니다.');
      } finally {
        select.disabled = false;
      }
    });
  }

  function bindThemePersistence() {
    const start = async () => {
      installThemePreferenceControl();
      await loadStoredThemePreference();
      const client = await themeSupabaseClient();
      if (!client || themeAuthSubscription) return;
      const { data } = client.auth.onAuthStateChange((event) => {
        if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED' || event === 'USER_UPDATED') {
          queueMicrotask(() => loadStoredThemePreference());
        } else if (event === 'SIGNED_OUT') {
          applyThemePreference('system');
        }
      });
      themeAuthSubscription = data?.subscription || true;
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
    else start();
  }

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

  installThemeStyles();
  applyThemePreference(cachedThemePreference(), { cache: false, announce: false });
  installLightweightChartsAdapter();
  bindThemePersistence();

  if (darkMedia?.addEventListener) {
    darkMedia.addEventListener('change', () => {
      if (themePreference === 'system') applyThemePreference('system', { cache: false });
    });
  } else if (darkMedia?.addListener) {
    darkMedia.addListener(() => {
      if (themePreference === 'system') applyThemePreference('system', { cache: false });
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
  window.MacroWatchTheme = Object.freeze({
    normalizeThemePreference,
    resolveTheme,
    getPreference: () => themePreference,
    applyPreference: applyThemePreference,
    loadStoredPreference: loadStoredThemePreference,
    savePreference: saveThemePreference,
    chartThemeOptions: lightweightThemeOptions,
  });
})();
