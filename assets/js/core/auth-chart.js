(() => {
  'use strict';

  const client = window.macroWatchSupabase || window.MacroWatchFrontend.createSupabaseClient();
  window.macroWatchSupabase = client;
  let accountUiBound = false;

  function functionClient() {
    return window.MacroWatchFrontend.createFunctionClient(client);
  }

  async function session() {
    const { data, error } = await client.auth.getSession();
    if (error) throw error;
    return data.session;
  }

  async function invoke(name, action, payload = {}) {
    return functionClient().invoke(name, { action, ...payload }, {
      errorMessage: status => `요청에 실패했습니다. (${status})`,
    });
  }

  function showServicePreparing() {
    const modal = document.getElementById('service-preparing-modal');
    modal?.classList.remove('hidden');
    document.getElementById('service-preparing-close')?.focus();
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

  async function loadKakaoStatus() {
    setKakaoStatus(false, '연결 상태 확인 중');
    try {
      const data = await invoke('kakao-auth', 'status');
      setKakaoStatus(Boolean(data?.connected));
    } catch (error) {
      setKakaoStatus(false, error.message || '연결 상태를 확인하지 못했습니다.');
    }
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

  async function loadEmailStatus() {
    setEmailStatus('', false, '설정 상태 확인 중');
    try {
      const data = await invoke('notification-settings', 'status');
      setEmailStatus(data?.address || '', Boolean(data?.is_active));
    } catch (error) {
      setEmailStatus('', false, error.message || '이메일 설정을 확인하지 못했습니다.');
    }
  }

  async function loadIdentity() {
    const output = document.getElementById('profile-username');
    if (!output) return;
    output.textContent = '확인 중';
    try {
      const current = await session();
      const userId = current?.user?.id;
      if (!userId) throw new Error('로그인이 필요합니다.');
      const { data, error } = await client.from('user_accounts').select('username').eq('user_id', userId).maybeSingle();
      if (error) throw error;
      output.textContent = data?.username || '아이디 미등록 · 카카오 전용 계정';
    } catch {
      output.textContent = '아이디를 확인하지 못했습니다.';
    }
  }

  function bindAccountModal() {
    if (accountUiBound) return;
    window.MacroWatchAccountModal.ensure();
    accountUiBound = true;

    const profile = document.getElementById('profile-modal');
    const close = () => profile?.classList.add('hidden');
    document.getElementById('profile-close-button')?.addEventListener('click', close);
    profile?.addEventListener('click', event => { if (event.target === profile) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !profile?.classList.contains('hidden')) close(); });

    const theme = document.getElementById('theme-preference');
    if (theme) {
      theme.value = window.MacroWatchTheme?.getPreference?.() || 'system';
      theme.addEventListener('change', async () => {
        theme.disabled = true;
        try { await window.MacroWatchTheme?.savePreference?.(theme.value); }
        catch (error) { window.alert(error?.message || '테마 설정을 저장하지 못했습니다.'); }
        finally { theme.disabled = false; }
      });
    }

    document.getElementById('password-change-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const current = document.getElementById('current-password').value;
      const next = document.getElementById('new-password').value;
      const confirm = document.getElementById('confirm-password').value;
      if (next.length < 6 || next !== confirm) {
        window.alert(next.length < 6 ? '새 비밀번호는 6자 이상이어야 합니다.' : '새 비밀번호가 서로 다릅니다.');
        return;
      }
      const currentSession = await session();
      const email = currentSession?.user?.email;
      const verified = await client.auth.signInWithPassword({ email, password: current });
      if (verified.error) { window.alert('현재 비밀번호가 올바르지 않습니다.'); return; }
      const { error } = await client.auth.updateUser({ password: next });
      if (error) { window.alert(error.message || '비밀번호를 변경하지 못했습니다.'); return; }
      event.currentTarget.reset();
      window.alert('비밀번호를 변경했습니다.');
    });

    document.getElementById('kakao-connect-button')?.addEventListener('click', showServicePreparing);
    document.getElementById('service-preparing-close')?.addEventListener('click', () => document.getElementById('service-preparing-modal')?.classList.add('hidden'));
    document.getElementById('kakao-unlink-button')?.addEventListener('click', async event => {
      if (!window.confirm('카카오 알림 연결을 해제할까요? ID 계정은 유지됩니다.')) return;
      event.currentTarget.disabled = true;
      try { await invoke('kakao-auth', 'unlink'); await loadKakaoStatus(); }
      catch (error) { window.alert(error.message || '카카오 연결을 해제하지 못했습니다.'); }
      finally { event.currentTarget.disabled = false; }
    });

    document.getElementById('email-alert-save-button')?.addEventListener('click', async event => {
      event.currentTarget.disabled = true;
      try {
        const address = document.getElementById('email-alert-address')?.value.trim() || '';
        await invoke('notification-settings', 'save', { address });
        await loadEmailStatus();
      } catch (error) { window.alert(error.message || '이메일 알림을 저장하지 못했습니다.'); }
      finally { event.currentTarget.disabled = false; }
    });
    document.getElementById('email-alert-remove-button')?.addEventListener('click', async event => {
      if (!window.confirm('이메일 알림을 해제할까요?')) return;
      event.currentTarget.disabled = true;
      try { await invoke('notification-settings', 'remove'); await loadEmailStatus(); }
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
        await client.auth.signOut({ scope: 'local' });
        window.location.replace('index.html');
      } catch (error) {
        window.alert(error.message || '회원 탈퇴를 처리하지 못했습니다.');
        event.currentTarget.disabled = false;
        event.currentTarget.textContent = '탈퇴하기';
      }
    });
  }

  async function openProfile() {
    bindAccountModal();
    document.getElementById('profile-modal')?.classList.remove('hidden');
    document.getElementById('profile-close-button')?.focus();
    await Promise.all([loadIdentity(), loadKakaoStatus(), loadEmailStatus()]);
  }

  async function updateAdminLink(currentSession) {
    const link = document.getElementById('admin-page-link');
    if (!link) return;
    link.hidden = true;
    const userId = currentSession?.user?.id;
    if (!userId) return;
    const { data, error } = await client.from('user_accounts').select('is_admin').eq('user_id', userId).maybeSingle();
    if (!error && data?.is_admin === true) link.hidden = false;
  }

  document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('logout-button')?.addEventListener('click', async () => {
      await client.auth.signOut({ scope: 'local' });
      window.location.replace('index.html');
    });
    document.getElementById('profile-button')?.addEventListener('click', () => openProfile().catch(error => console.error('profile open failed', error)));

    try {
      const currentSession = await session();
      if (!currentSession) {
        window.location.replace('index.html');
        return;
      }
      await updateAdminLink(currentSession);
    } catch (error) {
      console.error('chart session init failed', error);
    }
  });
})();
