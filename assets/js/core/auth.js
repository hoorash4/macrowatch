(() => {
  const KAKAO_OAUTH_STATE_KEY = 'macrowatch.kakao-oauth-state';
  const authClient = window.MacroWatchFrontend.createSupabaseClient();
  const functionClient = window.MacroWatchFrontend.createFunctionClient(authClient);
  window.macroWatchSupabase = authClient;
  const elements = {};
  let initialized = false;
  let accountControlsPromise = null;

  function setMessage(message = '') {
    elements.message.textContent = message;
  }

  function setBusy(isBusy, label = '카카오로 계속하기') {
    elements.submit.disabled = isBusy;
    elements.spinner.classList.toggle('hidden', !isBusy);
    elements.submitLabel.textContent = isBusy ? '카카오 연결 중' : label;
  }

  function invokeKakao(action, payload = {}) {
    return functionClient.invoke('kakao-auth', { action, ...payload }, {
      authenticated: action !== 'start' && action !== 'exchange',
      errorMessage: status => `카카오 요청에 실패했습니다. (${status})`,
    });
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
      refresh_token: tokens.refresh_token,
    });
    if (error || !data.session) throw error || new Error('로그인 세션을 저장하지 못했습니다.');
    window.history.replaceState({}, document.title, window.location.pathname);
    return data.session;
  }

  function loadSharedAccountControls() {
    if (window.MacroWatchAccountControls) return Promise.resolve(window.MacroWatchAccountControls);
    if (accountControlsPromise) return accountControlsPromise;
    accountControlsPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'assets/js/core/account-controls.js?v=2';
      script.onload = () => resolve(window.MacroWatchAccountControls);
      script.onerror = () => reject(new Error('공용 계정 기능을 불러오지 못했습니다.'));
      document.head.append(script);
    });
    return accountControlsPromise;
  }

  async function showDashboard() {
    elements.authScreen.classList.add('hidden');
    elements.appShell.classList.remove('hidden');
    const controls = await loadSharedAccountControls();
    await controls?.bind?.();
    await controls?.updateAdminLink?.();
    await window.MacroWatchDashboard?.loadAll();
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
    if (session) {
      const { data: account } = await authClient.from('user_accounts')
        .select('user_id').eq('user_id', session.user.id).maybeSingle();
      if (!account) {
        await authClient.auth.signOut({ scope: 'local' });
        showLogin('등록된 회원 계정을 확인하지 못했습니다.');
        return null;
      }
      await showDashboard();
      return session;
    }
    showLogin();
    return null;
  }

  function bindElements() {
    elements.authScreen = document.getElementById('auth-screen');
    elements.appShell = document.getElementById('app-shell');
    elements.form = document.getElementById('auth-form');
    elements.submit = document.getElementById('auth-submit');
    elements.submitLabel = document.getElementById('auth-submit-label');
    elements.spinner = document.getElementById('auth-spinner');
    elements.message = document.getElementById('auth-message');
  }

  function showServicePreparing() {
    const modal = document.getElementById('service-preparing-modal');
    const title = document.getElementById('service-preparing-title');
    const description = document.getElementById('service-preparing-description');
    modal?.classList.remove('is-auth-required');
    if (title) title.textContent = '서비스 준비 중입니다.';
    if (description) description.textContent = 'ID/PW 로그인과 회원가입은 추후 제공할 예정입니다.';
    modal?.classList.remove('hidden');
    document.getElementById('service-preparing-close')?.focus();
  }

  function showAuthRequired() {
    const modal = document.getElementById('service-preparing-modal');
    const title = document.getElementById('service-preparing-title');
    const description = document.getElementById('service-preparing-description');
    modal?.classList.add('is-auth-required');
    if (title) title.textContent = '로그인이 필요합니다.';
    if (description) description.textContent = '메뉴를 보려면 먼저 로그인해 주세요.';
    modal?.classList.remove('hidden');
    document.getElementById('service-preparing-close')?.focus();
  }

  function hideServicePreparing() {
    const modal = document.getElementById('service-preparing-modal');
    const shouldReturnToLogin = modal?.classList.contains('is-auth-required');
    modal?.classList.add('hidden');
    modal?.classList.remove('is-auth-required');
    if (shouldReturnToLogin) document.getElementById('login-id')?.focus();
  }

  function bindLoginEvents() {
    elements.form.addEventListener('submit', event => {
      event.preventDefault();
      showServicePreparing();
    });
    document.getElementById('password-login-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const button = document.getElementById('password-login-button');
      const username = document.getElementById('login-id').value.trim().toLowerCase();
      const password = document.getElementById('login-password').value;
      if (!/^[a-z0-9._-]{4,32}$/.test(username) || !password) {
        setMessage('아이디와 비밀번호를 확인해 주세요.');
        return;
      }
      button.disabled = true;
      setMessage('로그인 중입니다.');
      try {
        const { error } = await authClient.auth.signInWithPassword({
          email: `id-${username}@users.macrowatch.invalid`,
          password,
        });
        if (error) throw error;
        await verifyCurrentSession();
        setMessage();
      } catch {
        setMessage('아이디 또는 비밀번호가 올바르지 않습니다.');
      } finally {
        button.disabled = false;
      }
    });
    document.getElementById('signup-placeholder-button')?.addEventListener('click', showServicePreparing);
    document.querySelectorAll('[data-auth-dashboard-view]').forEach(button => {
      button.addEventListener('click', showAuthRequired);
    });
    document.getElementById('service-preparing-close')?.addEventListener('click', hideServicePreparing);
    document.getElementById('service-preparing-modal')?.addEventListener('click', event => {
      if (event.target === event.currentTarget) hideServicePreparing();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') hideServicePreparing();
    });
  }

  async function initialize() {
    bindElements();
    bindLoginEvents();
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

      if (code && state && session) {
        await showDashboard();
      } else if (code && state) {
        if (session) await authClient.auth.signOut();
        showLogin();
      }
    } catch (error) {
      window.history.replaceState({}, document.title, window.location.pathname);
      showLogin(error.message || '로그인하지 못했습니다.');
    }

    authClient.auth.onAuthStateChange(event => {
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
