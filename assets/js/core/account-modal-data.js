(() => {
  'use strict';

  if (!window.MacroWatchAccountModal || window.MacroWatchAccountModal.open) return;
  let actionsBound = false;
  let activeClient = null;
  let activeUserId = null;
  let activeFunctionClient = null;
  let activeThemeController = null;
  let activeReturnFocus = null;
  let activeDeleteRedirect = 'index.html';

  function invoke(name, action, payload = {}) {
    if (!activeFunctionClient?.invoke) throw new Error('계정 설정 호출 모듈을 초기화하지 못했습니다.');
    return activeFunctionClient.invoke(name, { action, ...payload }, {
      errorMessage: status => `요청에 실패했습니다. (${status})`,
    });
  }

  function setKakaoStatus(connected, message) {
    const status = document.getElementById('kakao-connection-status');
    const badge = document.getElementById('kakao-status-badge');
    const unlink = document.getElementById('kakao-unlink-button');
    if (status) status.textContent = message || (connected ? '로그인 한 카카오 계정으로 지표 변동 알림이 전송 됩니다.' : '카카오 알림 연결을 확인하지 못했습니다.');
    if (badge) {
      badge.textContent = connected ? '연결됨' : '확인 필요';
      badge.className = connected
        ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
        : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    }
    unlink?.classList.toggle('hidden', !connected);
  }

  function setEmailStatus(address, active, message) {
    const status = document.getElementById('email-alert-status');
    const badge = document.getElementById('email-alert-badge');
    const input = document.getElementById('email-alert-address');
    const remove = document.getElementById('email-alert-remove-button');
    if (status) status.textContent = message || (active ? `${address}로 지표 변동 알림을 받습니다.` : '이메일 알림을 설정하지 않았습니다.');
    if (badge) {
      badge.textContent = active ? '사용 중' : '미설정';
      badge.className = active
        ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
        : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    }
    if (input) input.value = address || '';
    remove?.classList.toggle('hidden', !active);
  }

  async function loadAccount(client, userId) {
    const username = document.getElementById('profile-username');
    if (username) username.textContent = '확인 중';
    const { data, error } = await client
      .from('user_accounts')
      .select('username,theme_preference')
      .eq('user_id', userId)
      .maybeSingle();
    if (error) throw error;
    if (username) username.textContent = data?.username || '아이디 미등록 · 카카오 전용 계정';
    const preference = data?.theme_preference || 'system';
    activeThemeController?.applyPreference?.(preference);
    const theme = document.getElementById('theme-preference');
    if (theme) theme.value = preference;
  }

  async function loadKakao() {
    setKakaoStatus(false, '연결 상태 확인 중');
    const data = await invoke('kakao-auth', 'status');
    setKakaoStatus(Boolean(data?.connected));
  }

  async function loadEmail() {
    setEmailStatus('', false, '설정 상태 확인 중');
    const data = await invoke('notification-settings', 'status');
    setEmailStatus(data?.address || '', Boolean(data?.is_active));
  }

  async function refresh() {
    if (!activeClient || !activeUserId) return;
    const results = await Promise.allSettled([
      loadAccount(activeClient, activeUserId),
      loadKakao(),
      loadEmail(),
    ]);
    if (results[0].status === 'rejected') {
      const username = document.getElementById('profile-username');
      if (username) username.textContent = '아이디를 확인하지 못했습니다.';
      console.error('account row load failed', results[0].reason);
    }
    if (results[1].status === 'rejected') {
      setKakaoStatus(false, results[1].reason?.message || '연결 상태를 확인하지 못했습니다.');
      console.error('kakao status load failed', results[1].reason);
    }
    if (results[2].status === 'rejected') {
      setEmailStatus('', false, results[2].reason?.message || '이메일 설정을 확인하지 못했습니다.');
      console.error('email status load failed', results[2].reason);
    }
  }

  function bindActions() {
    if (actionsBound) return;
    actionsBound = true;
    const modal = document.getElementById('profile-modal');
    const close = () => {
      modal?.classList.add('hidden');
      activeReturnFocus?.focus?.();
    };
    document.getElementById('profile-close-button')?.addEventListener('click', close);
    modal?.addEventListener('click', event => { if (event.target === modal) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !modal?.classList.contains('hidden')) close(); });

    const theme = document.getElementById('theme-preference');
    theme?.addEventListener('change', async () => {
      theme.disabled = true;
      try {
        if (!activeThemeController?.savePreference) throw new Error('테마 설정 모듈을 초기화하지 못했습니다.');
        await activeThemeController.savePreference(theme.value);
      }
      catch (error) { window.alert(error?.message || '테마 설정을 저장하지 못했습니다.'); }
      finally { theme.disabled = false; }
    });

    document.getElementById('password-change-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const current = document.getElementById('current-password')?.value || '';
      const next = document.getElementById('new-password')?.value || '';
      const confirm = document.getElementById('confirm-password')?.value || '';
      if (next.length < 6 || next !== confirm) {
        window.alert(next.length < 6 ? '새 비밀번호는 6자 이상이어야 합니다.' : '새 비밀번호가 서로 다릅니다.');
        return;
      }
      const { data } = await activeClient.auth.getSession();
      const email = data?.session?.user?.email;
      const verified = await activeClient.auth.signInWithPassword({ email, password: current });
      if (verified.error) { window.alert('현재 비밀번호가 올바르지 않습니다.'); return; }
      const { error } = await activeClient.auth.updateUser({ password: next });
      if (error) { window.alert(error.message || '비밀번호를 변경하지 못했습니다.'); return; }
      event.currentTarget.reset();
      window.alert('비밀번호를 변경했습니다.');
    });

    document.getElementById('kakao-connect-button')?.addEventListener('click', () => {
      const preparing = document.getElementById('service-preparing-modal');
      preparing?.classList.remove('hidden');
      document.getElementById('service-preparing-close')?.focus();
    });
    document.getElementById('service-preparing-close')?.addEventListener('click', () => document.getElementById('service-preparing-modal')?.classList.add('hidden'));
    document.getElementById('kakao-unlink-button')?.addEventListener('click', async event => {
      if (!window.confirm('카카오 알림 연결을 해제할까요? ID 계정은 유지됩니다.')) return;
      event.currentTarget.disabled = true;
      try { await invoke('kakao-auth', 'unlink'); await loadKakao(); }
      catch (error) { window.alert(error.message || '카카오 연결을 해제하지 못했습니다.'); }
      finally { event.currentTarget.disabled = false; }
    });

    document.getElementById('email-alert-save-button')?.addEventListener('click', async event => {
      event.currentTarget.disabled = true;
      try {
        const address = document.getElementById('email-alert-address')?.value.trim() || '';
        await invoke('notification-settings', 'save', { address });
        await loadEmail();
      } catch (error) { window.alert(error.message || '이메일 알림을 저장하지 못했습니다.'); }
      finally { event.currentTarget.disabled = false; }
    });
    document.getElementById('email-alert-remove-button')?.addEventListener('click', async event => {
      if (!window.confirm('이메일 알림을 해제할까요?')) return;
      event.currentTarget.disabled = true;
      try { await invoke('notification-settings', 'remove'); await loadEmail(); }
      catch (error) { window.alert(error.message || '이메일 설정을 해제하지 못했습니다.'); }
      finally { event.currentTarget.disabled = false; }
    });

    document.getElementById('account-delete-button')?.addEventListener('click', () => document.getElementById('account-delete-modal')?.classList.remove('hidden'));
    document.getElementById('account-delete-cancel')?.addEventListener('click', () => document.getElementById('account-delete-modal')?.classList.add('hidden'));
    document.getElementById('account-delete-confirm')?.addEventListener('click', async event => {
      event.currentTarget.disabled = true;
      event.currentTarget.textContent = '처리 중';
      try {
        await invoke('kakao-auth', 'delete_account');
        await activeClient.auth.signOut({ scope: 'local' });
        window.location.replace(activeDeleteRedirect);
      } catch (error) {
        window.alert(error.message || '회원 탈퇴를 처리하지 못했습니다.');
        event.currentTarget.disabled = false;
        event.currentTarget.textContent = '탈퇴하기';
      }
    });
  }

  async function open({
    client,
    userId,
    functionClient = window.MacroWatchFrontend?.createFunctionClient?.(client),
    themeController = window.MacroWatchTheme,
    returnFocus = null,
    deleteRedirect = 'index.html',
  }) {
    if (!client || !userId) throw new Error('개인 설정에 필요한 로그인 정보를 받지 못했습니다.');
    if (!functionClient?.invoke) throw new Error('개인 설정 호출 모듈을 초기화하지 못했습니다.');
    activeClient = client;
    activeUserId = userId;
    activeFunctionClient = functionClient;
    activeThemeController = themeController;
    activeReturnFocus = returnFocus;
    activeDeleteRedirect = deleteRedirect;
    window.MacroWatchAccountModal.ensure();
    window.MacroWatchAccountModal.normalize?.();
    bindActions();
    const modal = document.getElementById('profile-modal');
    modal?.classList.remove('hidden');
    document.getElementById('profile-close-button')?.focus();
    await refresh();
  }

  function bindTrigger({ button, client, deleteRedirect = 'index.html' }) {
    if (!button || !client || button.dataset.accountModalBound === 'true') return;
    button.dataset.accountModalBound = 'true';
    button.addEventListener('click', async () => {
      try {
        const { data, error } = await client.auth.getSession();
        if (error) throw error;
        const userId = data?.session?.user?.id;
        if (!userId) throw new Error('로그인이 필요합니다.');
        await open({ client, userId, returnFocus: button, deleteRedirect });
      } catch (error) {
        console.error('profile open failed', error);
        window.alert(error?.message || '개인 설정을 열지 못했습니다.');
      }
    });
  }

  window.MacroWatchAccountModal.open = open;
  window.MacroWatchAccountModal.bindTrigger = bindTrigger;
})();
