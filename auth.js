(() => {
  const { supabaseUrl: AUTH_SUPABASE_URL, supabasePublishableKey: AUTH_SUPABASE_KEY } = window.MACROWATCH_CONFIG;
  const KAKAO_OAUTH_STATE_KEY = 'macrowatch.kakao-oauth-state';
  const authClient = window.supabase?.createClient(AUTH_SUPABASE_URL, AUTH_SUPABASE_KEY);
  window.macroWatchSupabase = authClient;
  const elements = {};
  let initialized = false;

  function setMessage(message = '') {
    elements.message.textContent = message;
  }

  function setBusy(isBusy, label = '카카오로 계속하기') {
    elements.submit.disabled = isBusy;
    elements.spinner.classList.toggle('hidden', !isBusy);
    elements.submitLabel.textContent = isBusy ? '카카오 연결 중' : label;
  }

  async function getAccessToken() {
    const { data, error } = await authClient.auth.getSession();
    if (error || !data.session?.access_token) {
      throw new Error('로그인이 필요합니다.');
    }
    return data.session.access_token;
  }

  async function invokeKakao(action, payload = {}, retried = false) {
    const requiresAuth = action !== 'start' && action !== 'exchange';
    const token = requiresAuth ? await getAccessToken() : AUTH_SUPABASE_KEY;
    const response = await fetch(`${AUTH_SUPABASE_URL}/functions/v1/kakao-auth`, {
      method: 'POST',
      headers: {
        apikey: AUTH_SUPABASE_KEY,
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ action, ...payload })
    });
    const data = await response.json().catch(() => ({}));
    if (requiresAuth && response.status === 401 && !retried) {
      const refreshed = await authClient.auth.refreshSession();
      if (!refreshed.error && refreshed.data.session) {
        return invokeKakao(action, payload, true);
      }
    }
    if (!response.ok) throw new Error(data?.error || `카카오 요청에 실패했습니다. (${response.status})`);
    if (data?.error) throw new Error(data.error);
    return data;
  }

  async function beginKakaoLogin() {
    setBusy(true);
    setMessage();
    try {
      const data = await invokeKakao('start');
      if (!data?.authorize_url || !data?.state) throw new Error('카카오 로그인 정보를 받지 못했습니다.');
      window.sessionStorage.setItem(KAKAO_OAUTH_STATE_KEY, data.state);
      window.location.assign(data.authorize_url);
    } catch (error) {
      setMessage(error.message || '카카오 로그인을 시작하지 못했습니다.');
      setBusy(false);
    }
  }

  async function finishKakaoLogin(code, state) {
    setBusy(true);
    setMessage('카카오 로그인을 완료하는 중입니다.');
    const expectedState = window.sessionStorage.getItem(KAKAO_OAUTH_STATE_KEY);
    window.sessionStorage.removeItem(KAKAO_OAUTH_STATE_KEY);
    if (!expectedState || expectedState !== state) {
      throw new Error('카카오 로그인 요청을 확인할 수 없습니다. 다시 시도해 주세요.');
    }
    const tokens = await invokeKakao('exchange', { code, state });
    const { data, error } = await authClient.auth.setSession({
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token
    });
    if (error || !data.session) throw error || new Error('로그인 세션을 저장하지 못했습니다.');
    window.history.replaceState({}, document.title, window.location.pathname);
    return data.session;
  }

  async function updateAdminLink() {
    const link = document.getElementById('admin-page-link');
    if (!link) return;
    link.classList.add('hidden');
    const { data: sessionData } = await authClient.auth.getSession();
    const userId = sessionData.session?.user?.id;
    if (!userId) return;
    const { data, error } = await authClient
      .from('user_accounts')
      .select('is_admin')
      .eq('user_id', userId)
      .maybeSingle();
    if (!error && data?.is_admin === true) link.classList.remove('hidden');
  }

  async function showDashboard() {
    elements.authScreen.classList.add('hidden');
    elements.appShell.classList.remove('hidden');
    await updateAdminLink();
    await Promise.all([
      window.fetchTargets?.(),
      window.loadNewsSentimentDashboard?.(),
      window.loadMarketStressDashboard?.(),
      window.loadCreditStressComponentsDashboard?.(),
      window.loadKoreaStressDashboard?.(),
    ]);
  }

  function showLogin(message = '') {
    elements.appShell.classList.add('hidden');
    elements.authScreen.classList.remove('hidden');
    setBusy(false);
    setMessage(message);
  }

  async function verifyCurrentSession() {
    if (!authClient) return null;
    const { data, error } = await authClient.auth.getSession();
    if (error) throw error;
    const session = data.session;
    if (session?.user?.user_metadata?.auth_provider === 'kakao') {
      await showDashboard();
      return session;
    }
    if (session) await authClient.auth.signOut({ scope: 'local' });
    showLogin();
    return null;
  }

  function setKakaoStatus(connected, message) {
    elements.kakaoStatus.textContent = message || (connected ? '로그인 한 카카오 계정으로 지표 변동 알림이 전송 됩니다.' : '카카오 알림 연결을 확인하지 못했습니다.');
    elements.kakaoBadge.textContent = connected ? '연결됨' : '확인 필요';
    elements.kakaoBadge.className = connected
      ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
      : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    elements.kakaoConnectButton.querySelector('span').textContent = '카카오 계정 다시 연결하기';
  }

  async function loadKakaoStatus() {
    setKakaoStatus(false, '연결 상태 확인 중');
    elements.kakaoConnectButton.disabled = true;
    try {
      const data = await invokeKakao('status');
      setKakaoStatus(Boolean(data?.connected));
    } catch (error) {
      setKakaoStatus(false, error.message || '연결 상태를 확인하지 못했습니다.');
    } finally {
      elements.kakaoConnectButton.disabled = false;
    }
  }

  async function initialize() {
    elements.authScreen = document.getElementById('auth-screen');
    elements.appShell = document.getElementById('app-shell');
    elements.form = document.getElementById('auth-form');
    elements.submit = document.getElementById('auth-submit');
    elements.submitLabel = document.getElementById('auth-submit-label');
    elements.spinner = document.getElementById('auth-spinner');
    elements.message = document.getElementById('auth-message');
    elements.profileModal = document.getElementById('profile-modal');
    elements.kakaoStatus = document.getElementById('kakao-connection-status');
    elements.kakaoBadge = document.getElementById('kakao-status-badge');
    elements.kakaoConnectButton = document.getElementById('kakao-connect-button');

    elements.form.addEventListener('submit', (event) => {
      event.preventDefault();
      beginKakaoLogin();
    });
    const showServicePreparing = () => {
      document.getElementById('service-preparing-modal')?.classList.remove('hidden');
      document.getElementById('service-preparing-close')?.focus();
    };
    const hideServicePreparing = () => {
      document.getElementById('service-preparing-modal')?.classList.add('hidden');
    };
    document.getElementById('password-login-form')?.addEventListener('submit', (event) => {
      event.preventDefault();
      showServicePreparing();
    });
    document.getElementById('signup-placeholder-button')?.addEventListener('click', showServicePreparing);
    document.getElementById('service-preparing-close')?.addEventListener('click', hideServicePreparing);
    document.getElementById('service-preparing-modal')?.addEventListener('click', (event) => {
      if (event.target === event.currentTarget) hideServicePreparing();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') hideServicePreparing();
    });
    document.getElementById('profile-button')?.addEventListener('click', async () => {
      elements.profileModal.classList.remove('hidden');
      await loadKakaoStatus();
    });
    document.getElementById('profile-close-button')?.addEventListener('click', () => {
      elements.profileModal.classList.add('hidden');
    });
    elements.kakaoConnectButton.addEventListener('click', beginKakaoLogin);
    document.getElementById('account-delete-button')?.addEventListener('click', () => {
      document.getElementById('account-delete-modal')?.classList.remove('hidden');
    });
    document.getElementById('account-delete-cancel')?.addEventListener('click', () => {
      document.getElementById('account-delete-modal')?.classList.add('hidden');
    });
    document.getElementById('account-delete-confirm')?.addEventListener('click', async () => {
      const button = document.getElementById('account-delete-confirm');
      button.disabled = true;
      button.textContent = '처리 중';
      try {
        await invokeKakao('delete_account');
        await authClient.auth.signOut({ scope: 'local' });
        window.location.replace('./');
      } catch (error) {
        window.alert(error.message || '회원 탈퇴를 처리하지 못했습니다.');
        button.disabled = false;
        button.textContent = '탈퇴하기';
      }
    });
    document.getElementById('logout-button')?.addEventListener('click', async () => {
      await authClient?.auth.signOut();
      showLogin();
    });

    if (!authClient) {
      showLogin('로그인 기능을 불러오지 못했습니다.');
      return;
    }

    try {
      const params = new URLSearchParams(window.location.search);
      const code = params.get('code');
      const state = params.get('state');
      const oauthError = params.get('error_description') || params.get('error');
      let session = null;

      if (code && state) {
        session = await finishKakaoLogin(code, state);
      } else if (oauthError) {
        window.history.replaceState({}, document.title, window.location.pathname);
        throw new Error('카카오 로그인이 취소되었습니다.');
      } else {
        session = await verifyCurrentSession();
      }

      if (code && state && session?.user?.user_metadata?.auth_provider === 'kakao') {
        await showDashboard();
      } else if (code && state) {
        if (session) await authClient.auth.signOut();
        showLogin();
      }
    } catch (error) {
      window.history.replaceState({}, document.title, window.location.pathname);
      showLogin(error.message || '로그인하지 못했습니다.');
    }

    authClient.auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_OUT') showLogin();
    });
    initialized = true;
  }

  document.addEventListener('DOMContentLoaded', initialize);
  window.addEventListener('pageshow', async () => {
    if (!initialized) return;
    try {
      await verifyCurrentSession();
    } catch (error) {
      showLogin(error.message || '로그인 상태를 확인하지 못했습니다.');
    }
  });
})();
