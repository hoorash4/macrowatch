(() => {
  const KAKAO_OAUTH_STATE_KEY = 'macrowatch.kakao-oauth-state';
  const authClient = window.macroWatchSupabase || window.MacroWatchFrontend.createSupabaseClient();
  const functionClient = window.MacroWatchFrontend.createFunctionClient(authClient);
  window.macroWatchSupabase = authClient;
  const elements = {};
  let initialized = false;

  function setMessage(message = '') {
    if (elements.message) elements.message.textContent = message;
  }

  function setBusy(isBusy, label = '카카오로 계속하기') {
    if (!elements.submit || !elements.spinner || !elements.submitLabel) return;
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

  function invokeEmailSettings(action, payload = {}) {
    return functionClient.invoke('notification-settings', { action, ...payload }, {
      errorMessage: status => `이메일 설정 요청에 실패했습니다. (${status})`,
    });
  }

  async function ensureAccountUi() {
    if (document.getElementById('profile-modal') && document.getElementById('account-delete-modal') && document.getElementById('service-preparing-modal')) return;
    const response = await fetch('index.html', { cache: 'no-cache' });
    if (!response.ok) throw new Error(`개인 설정 화면을 불러오지 못했습니다. (${response.status})`);
    const source = new DOMParser().parseFromString(await response.text(), 'text/html');
    for (const id of ['service-preparing-modal', 'profile-modal', 'account-delete-modal']) {
      if (document.getElementById(id)) continue;
      const node = source.getElementById(id);
      if (!node) throw new Error(`공용 계정 화면을 찾지 못했습니다: ${id}`);
      document.body.append(document.importNode(node, true));
    }
  }

  function ensureThemePreferenceControl() {
    if (document.getElementById('theme-preference')) return;
    const body = document.querySelector('#profile-modal .profile-dialog-body');
    const deleteSection = document.getElementById('account-delete-button')?.closest('.rounded-xl');
    if (!body || !deleteSection) return;
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
    body.insertBefore(card, deleteSection);
    const select = card.querySelector('#theme-preference');
    select.value = window.MacroWatchTheme?.getPreference?.() || 'system';
    select.addEventListener('change', async () => {
      select.disabled = true;
      try { await window.MacroWatchTheme?.savePreference?.(select.value); }
      catch (error) { window.alert(error?.message || '테마 설정을 저장하지 못했습니다.'); }
      finally { select.disabled = false; }
    });
  }

  function normalizeProfileModal() {
    ensureThemePreferenceControl();
    const modal = document.getElementById('profile-modal');
    const deleteModal = document.getElementById('account-delete-modal');
    const body = modal?.querySelector('.profile-dialog-body');
    const themeCard = document.getElementById('theme-preference')?.closest('.theme-preference-card');
    const deleteSection = document.getElementById('account-delete-button')?.closest('.rounded-xl');
    if (body && themeCard && deleteSection?.parentElement === body && themeCard.nextElementSibling !== deleteSection) {
      body.insertBefore(themeCard, deleteSection);
    }
    for (const root of [modal, deleteModal]) {
      if (!root) continue;
      root.style.colorScheme = 'dark';
      root.style.setProperty('--theme-surface', '#0f172a');
      root.style.setProperty('--theme-surface-elevated', '#0f172a');
      root.style.setProperty('--theme-text', '#e2e8f0');
      root.style.setProperty('--theme-text-secondary', '#94a3b8');
      root.style.setProperty('--theme-text-muted', '#64748b');
      root.style.setProperty('--theme-border', '#334155');
      root.style.setProperty('--theme-input-bg', '#0f172a');
      root.style.setProperty('--theme-input-border', '#334155');
    }
    if (themeCard) {
      themeCard.style.background = 'rgba(2, 6, 23, .6)';
      themeCard.style.borderColor = '#1e293b';
      const label = themeCard.querySelector('label');
      const help = themeCard.querySelector('p');
      const select = themeCard.querySelector('select');
      if (label) label.style.color = '#cbd5e1';
      if (help) help.style.color = '#64748b';
      if (select) {
        select.style.background = '#0f172a';
        select.style.borderColor = '#334155';
        select.style.color = '#e2e8f0';
      }
    }
  }

  async function finishKakaoLogin(code, state) {
    setBusy(true);
    setMessage('카카오 로그인을 완료하는 중입니다.');
    const expectedState = window.sessionStorage.getItem(KAKAO_OAUTH_STATE_KEY);
    window.sessionStorage.removeItem(KAKAO_OAUTH_STATE_KEY);
    if (!expectedState || expectedState !== state) throw new Error('카카오 로그인 요청을 확인할 수 없습니다. 다시 시도해 주세요.');
    const tokens = await invokeKakao('exchange', { code, state });
    const { data, error } = await authClient.auth.setSession({ access_token: tokens.access_token, refresh_token: tokens.refresh_token });
    if (error || !data.session) throw error || new Error('로그인 세션을 저장하지 못했습니다.');
    window.history.replaceState({}, document.title, window.location.pathname);
    return data.session;
  }

  async function updateAdminLink() {
    const link = document.getElementById('admin-page-link');
    if (!link) return;
    link.hidden = true;
    const { data: sessionData } = await authClient.auth.getSession();
    const userId = sessionData.session?.user?.id;
    if (!userId) return;
    const { data, error } = await authClient.from('user_accounts').select('is_admin').eq('user_id', userId).maybeSingle();
    if (!error && data?.is_admin === true) link.hidden = false;
  }

  async function showDashboard() {
    elements.authScreen.classList.add('hidden');
    elements.appShell.classList.remove('hidden');
    await updateAdminLink();
    await window.MacroWatchDashboard?.loadAll();
  }

  function showLogin(message = '') {
    if (!elements.appShell || !elements.authScreen) return;
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
      const { data: account } = await authClient.from('user_accounts').select('user_id').eq('user_id', session.user.id).maybeSingle();
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

  function setKakaoStatus(connected, message) {
    elements.kakaoStatus.textContent = message || (connected ? '로그인 한 카카오 계정으로 지표 변동 알림이 전송 됩니다.' : '카카오 알림 연결을 확인하지 못했습니다.');
    elements.kakaoBadge.textContent = connected ? '연결됨' : '확인 필요';
    elements.kakaoBadge.className = connected
      ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
      : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    elements.kakaoConnectButton.querySelector('span').textContent = '카카오 계정 다시 연결하기';
    elements.kakaoUnlinkButton.classList.toggle('hidden', !connected);
  }

  async function loadKakaoStatus() {
    setKakaoStatus(false, '연결 상태 확인 중');
    elements.kakaoConnectButton.disabled = true;
    try { const data = await invokeKakao('status'); setKakaoStatus(Boolean(data?.connected)); }
    catch (error) { setKakaoStatus(false, error.message || '연결 상태를 확인하지 못했습니다.'); }
    finally { elements.kakaoConnectButton.disabled = false; }
  }

  function setEmailStatus(address, active, message) {
    elements.emailStatus.textContent = message || (active ? `${address}로 지표 변동 알림을 받습니다.` : '이메일 알림을 설정하지 않았습니다.');
    elements.emailBadge.textContent = active ? '사용 중' : '미설정';
    elements.emailBadge.className = active
      ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
      : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    elements.emailAddress.value = address || '';
    elements.emailRemoveButton.classList.toggle('hidden', !active);
  }

  async function loadEmailStatus() {
    setEmailStatus('', false, '설정 상태 확인 중');
    try { const data = await invokeEmailSettings('status'); setEmailStatus(data?.address || '', Boolean(data?.is_active)); }
    catch (error) { setEmailStatus('', false, error.message || '이메일 설정을 확인하지 못했습니다.'); }
  }

  async function loadProfileIdentity() {
    elements.profileUsername.textContent = '확인 중';
    try {
      const { data: sessionData } = await authClient.auth.getSession();
      const userId = sessionData.session?.user?.id;
      if (!userId) throw new Error('로그인이 필요합니다.');
      const { data, error } = await authClient.from('user_accounts').select('username').eq('user_id', userId).maybeSingle();
      if (error) throw error;
      elements.profileUsername.textContent = data?.username || '아이디 미등록 · 카카오 전용 계정';
    } catch {
      elements.profileUsername.textContent = '아이디를 확인하지 못했습니다.';
    }
  }

  function bindElements() {
    elements.authScreen = document.getElementById('auth-screen');
    elements.appShell = document.getElementById('app-shell');
    elements.form = document.getElementById('auth-form');
    elements.submit = document.getElementById('auth-submit');
    elements.submitLabel = document.getElementById('auth-submit-label');
    elements.spinner = document.getElementById('auth-spinner');
    elements.message = document.getElementById('auth-message');
    elements.profileModal = document.getElementById('profile-modal');
    elements.profileUsername = document.getElementById('profile-username');
    elements.kakaoStatus = document.getElementById('kakao-connection-status');
    elements.kakaoBadge = document.getElementById('kakao-status-badge');
    elements.kakaoConnectButton = document.getElementById('kakao-connect-button');
    elements.kakaoUnlinkButton = document.getElementById('kakao-unlink-button');
    elements.emailStatus = document.getElementById('email-alert-status');
    elements.emailBadge = document.getElementById('email-alert-badge');
    elements.emailAddress = document.getElementById('email-alert-address');
    elements.emailSaveButton = document.getElementById('email-alert-save-button');
    elements.emailRemoveButton = document.getElementById('email-alert-remove-button');
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
    if (!elements.form) return;
    elements.form.addEventListener('submit', event => { event.preventDefault(); showServicePreparing(); });
    document.getElementById('password-login-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const button = document.getElementById('password-login-button');
      const username = document.getElementById('login-id').value.trim().toLowerCase();
      const password = document.getElementById('login-password').value;
      if (!/^[a-z0-9._-]{4,32}$/.test(username) || !password) { setMessage('아이디와 비밀번호를 확인해 주세요.'); return; }
      button.disabled = true;
      setMessage('로그인 중입니다.');
      try {
        const { error } = await authClient.auth.signInWithPassword({ email: `id-${username}@users.macrowatch.invalid`, password });
        if (error) throw error;
        await verifyCurrentSession();
        setMessage();
      } catch { setMessage('아이디 또는 비밀번호가 올바르지 않습니다.'); }
      finally { button.disabled = false; }
    });
    document.getElementById('signup-placeholder-button')?.addEventListener('click', showServicePreparing);
    document.querySelectorAll('[data-auth-dashboard-view]').forEach(button => button.addEventListener('click', showAuthRequired));
    document.getElementById('service-preparing-close')?.addEventListener('click', hideServicePreparing);
    document.getElementById('service-preparing-modal')?.addEventListener('click', event => { if (event.target === event.currentTarget) hideServicePreparing(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') hideServicePreparing(); });
  }

  function bindProfileEvents() {
    const closeProfileModal = () => {
      elements.profileModal.classList.add('hidden');
      document.getElementById('profile-button')?.focus();
    };
    document.getElementById('profile-button')?.addEventListener('click', async () => {
      normalizeProfileModal();
      elements.profileModal.classList.remove('hidden');
      document.getElementById('profile-close-button')?.focus();
      await Promise.all([loadProfileIdentity(), loadKakaoStatus(), loadEmailStatus()]);
    });
    document.getElementById('profile-close-button')?.addEventListener('click', closeProfileModal);
    elements.profileModal.addEventListener('click', event => { if (event.target === event.currentTarget) closeProfileModal(); });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !elements.profileModal.classList.contains('hidden')) closeProfileModal();
    });
    window.addEventListener('macrowatch:themechange', normalizeProfileModal);
    elements.kakaoConnectButton.addEventListener('click', showServicePreparing);
    elements.kakaoUnlinkButton.addEventListener('click', async () => {
      if (!window.confirm('카카오 알림 연결을 해제할까요? ID 계정은 유지됩니다.')) return;
      elements.kakaoUnlinkButton.disabled = true;
      try { await invokeKakao('unlink'); await loadKakaoStatus(); }
      catch (error) { window.alert(error.message || '카카오 연결을 해제하지 못했습니다.'); }
      finally { elements.kakaoUnlinkButton.disabled = false; }
    });
    elements.emailSaveButton.addEventListener('click', async () => {
      elements.emailSaveButton.disabled = true;
      try { await invokeEmailSettings('save', { address: elements.emailAddress.value.trim() }); await loadEmailStatus(); }
      catch (error) { window.alert(error.message || '이메일 알림을 저장하지 못했습니다.'); }
      finally { elements.emailSaveButton.disabled = false; }
    });
    elements.emailRemoveButton.addEventListener('click', async () => {
      if (!window.confirm('이메일 알림을 해제할까요?')) return;
      elements.emailRemoveButton.disabled = true;
      try { await invokeEmailSettings('remove'); await loadEmailStatus(); }
      catch (error) { window.alert(error.message || '이메일 설정을 해제하지 못했습니다.'); }
      finally { elements.emailRemoveButton.disabled = false; }
    });
  }

  function bindAccountEvents() {
    document.getElementById('password-change-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const current = document.getElementById('current-password').value;
      const next = document.getElementById('new-password').value;
      const confirm = document.getElementById('confirm-password').value;
      if (next.length < 6 || next !== confirm) { window.alert(next.length < 6 ? '새 비밀번호는 6자 이상이어야 합니다.' : '새 비밀번호가 서로 다릅니다.'); return; }
      const { data: sessionData } = await authClient.auth.getSession();
      const email = sessionData.session?.user?.email;
      const verified = await authClient.auth.signInWithPassword({ email, password: current });
      if (verified.error) { window.alert('현재 비밀번호가 올바르지 않습니다.'); return; }
      const { error } = await authClient.auth.updateUser({ password: next });
      if (error) { window.alert(error.message || '비밀번호를 변경하지 못했습니다.'); return; }
      event.currentTarget.reset();
      window.alert('비밀번호를 변경했습니다.');
    });
    document.getElementById('account-delete-button')?.addEventListener('click', () => document.getElementById('account-delete-modal')?.classList.remove('hidden'));
    document.getElementById('account-delete-cancel')?.addEventListener('click', () => document.getElementById('account-delete-modal')?.classList.add('hidden'));
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
      await authClient.auth.signOut();
      if (elements.authScreen) showLogin();
      else window.location.replace('index.html');
    });
  }

  async function bindAccountUi() {
    await ensureAccountUi();
    normalizeProfileModal();
    bindElements();
    bindProfileEvents();
    bindAccountEvents();
    await updateAdminLink();
  }

  async function initialize() {
    const hasMainLogin = Boolean(document.getElementById('auth-screen'));
    if (!authClient) return;
    if (!hasMainLogin) {
      await bindAccountUi();
      initialized = true;
      return;
    }

    bindElements();
    bindLoginEvents();
    try {
      const params = new URLSearchParams(window.location.search);
      const code = params.get('code');
      const state = params.get('state');
      const oauthError = params.get('error_description') || params.get('error');
      let session = null;
      if (code && state) session = await finishKakaoLogin(code, state);
      else if (oauthError) {
        window.history.replaceState({}, document.title, window.location.pathname);
        throw new Error('카카오 로그인이 취소되었습니다.');
      } else session = await verifyCurrentSession();

      if (code && state && session) await showDashboard();
      else if (code && state) {
        if (session) await authClient.auth.signOut();
        showLogin();
      }
      if (session || (!code && !state && !oauthError)) await bindAccountUi();
    } catch (error) {
      window.history.replaceState({}, document.title, window.location.pathname);
      showLogin(error.message || '로그인하지 못했습니다.');
    }
    authClient.auth.onAuthStateChange(event => { if (event === 'SIGNED_OUT') showLogin(); });
    initialized = true;
  }

  document.addEventListener('DOMContentLoaded', () => initialize().catch(error => console.error('auth init failed', error)));
  window.addEventListener('pageshow', async () => {
    if (!initialized || !elements.authScreen) return;
    try { await verifyCurrentSession(); }
    catch (error) { showLogin(error.message || '로그인 상태를 확인하지 못했습니다.'); }
  });
})();
