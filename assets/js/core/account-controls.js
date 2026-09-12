(() => {
  'use strict';

  let bound = false;
  const getClient = () => window.macroWatchSupabase || null;
  const getFunctionClient = () => {
    const client = getClient();
    return client ? window.MacroWatchFrontend?.createFunctionClient?.(client) : null;
  };

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

  function normalizeProfileModal() {
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

  async function currentUser() {
    const client = getClient();
    if (!client) return null;
    const { data, error } = await client.auth.getSession();
    if (error) throw error;
    return data.session?.user || null;
  }

  async function invoke(name, action, payload = {}) {
    const functions = getFunctionClient();
    if (!functions) throw new Error('설정 기능을 불러오지 못했습니다.');
    return functions.invoke(name, { action, ...payload }, {
      errorMessage: status => `${name} 요청에 실패했습니다. (${status})`,
    });
  }

  function setKakaoStatus(connected, message) {
    const status = document.getElementById('kakao-connection-status');
    const badge = document.getElementById('kakao-status-badge');
    const unlink = document.getElementById('kakao-unlink-button');
    const connect = document.getElementById('kakao-connect-button');
    if (!status || !badge || !unlink) return;
    status.textContent = message || (connected ? '로그인 한 카카오 계정으로 지표 변동 알림이 전송 됩니다.' : '카카오 알림 연결을 확인하지 못했습니다.');
    badge.textContent = connected ? '연결됨' : '확인 필요';
    badge.className = connected
      ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
      : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    if (connect?.querySelector('span')) connect.querySelector('span').textContent = '카카오 계정 다시 연결하기';
    unlink.classList.toggle('hidden', !connected);
  }

  async function loadKakaoStatus() {
    const button = document.getElementById('kakao-connect-button');
    setKakaoStatus(false, '연결 상태 확인 중');
    if (button) button.disabled = true;
    try {
      const data = await invoke('kakao-auth', 'status');
      setKakaoStatus(Boolean(data?.connected));
    } catch (error) {
      setKakaoStatus(false, error.message || '연결 상태를 확인하지 못했습니다.');
    } finally {
      if (button) button.disabled = false;
    }
  }

  function setEmailStatus(address, active, message) {
    const status = document.getElementById('email-alert-status');
    const badge = document.getElementById('email-alert-badge');
    const input = document.getElementById('email-alert-address');
    const remove = document.getElementById('email-alert-remove-button');
    if (!status || !badge || !input || !remove) return;
    status.textContent = message || (active ? `${address}로 지표 변동 알림을 받습니다.` : '이메일 알림을 설정하지 않았습니다.');
    badge.textContent = active ? '사용 중' : '미설정';
    badge.className = active
      ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
      : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    input.value = address || '';
    remove.classList.toggle('hidden', !active);
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

  async function loadProfileIdentity() {
    const target = document.getElementById('profile-username');
    if (!target) return;
    target.textContent = '확인 중';
    try {
      const user = await currentUser();
      if (!user) throw new Error('로그인이 필요합니다.');
      const { data, error } = await getClient().from('user_accounts').select('username').eq('user_id', user.id).maybeSingle();
      if (error) throw error;
      target.textContent = data?.username || '아이디 미등록 · 카카오 전용 계정';
    } catch {
      target.textContent = '아이디를 확인하지 못했습니다.';
    }
  }

  async function updateAdminLink() {
    const link = document.getElementById('admin-page-link');
    if (!link) return;
    link.hidden = true;
    const user = await currentUser();
    if (!user) return;
    const { data, error } = await getClient().from('user_accounts').select('is_admin').eq('user_id', user.id).maybeSingle();
    if (!error && data?.is_admin === true) link.hidden = false;
  }

  function showServicePreparing() {
    const modal = document.getElementById('service-preparing-modal');
    if (!modal) return;
    document.getElementById('service-preparing-title').textContent = '서비스 준비 중입니다.';
    document.getElementById('service-preparing-description').textContent = 'ID/PW 로그인과 회원가입은 추후 제공할 예정입니다.';
    modal.classList.remove('hidden');
    document.getElementById('service-preparing-close')?.focus();
  }

  async function bind() {
    if (bound) return;
    const client = getClient();
    if (!client) return;
    await ensureAccountUi();
    normalizeProfileModal();
    const modal = document.getElementById('profile-modal');
    if (!modal) return;
    bound = true;

    const close = () => {
      modal.classList.add('hidden');
      document.getElementById('profile-button')?.focus();
    };

    document.getElementById('profile-button')?.addEventListener('click', async () => {
      normalizeProfileModal();
      modal.classList.remove('hidden');
      document.getElementById('profile-close-button')?.focus();
      await Promise.all([loadProfileIdentity(), loadKakaoStatus(), loadEmailStatus()]);
    });
    document.getElementById('profile-close-button')?.addEventListener('click', close);
    modal.addEventListener('click', event => { if (event.target === event.currentTarget) close(); });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !modal.classList.contains('hidden')) close();
    });
    window.addEventListener('macrowatch:themechange', normalizeProfileModal);

    document.getElementById('kakao-connect-button')?.addEventListener('click', showServicePreparing);
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
        await invoke('notification-settings', 'save', { address: document.getElementById('email-alert-address')?.value.trim() || '' });
        await loadEmailStatus();
      } catch (error) {
        window.alert(error.message || '이메일 알림을 저장하지 못했습니다.');
      } finally {
        event.currentTarget.disabled = false;
      }
    });
    document.getElementById('email-alert-remove-button')?.addEventListener('click', async event => {
      if (!window.confirm('이메일 알림을 해제할까요?')) return;
      event.currentTarget.disabled = true;
      try { await invoke('notification-settings', 'remove'); await loadEmailStatus(); }
      catch (error) { window.alert(error.message || '이메일 알림을 해제하지 못했습니다.'); }
      finally { event.currentTarget.disabled = false; }
    });
    document.getElementById('password-change-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const currentPassword = document.getElementById('current-password')?.value || '';
      const next = document.getElementById('new-password')?.value || '';
      const confirm = document.getElementById('confirm-password')?.value || '';
      if (next.length < 6 || next !== confirm) {
        window.alert(next.length < 6 ? '새 비밀번호는 6자 이상이어야 합니다.' : '새 비밀번호가 서로 다릅니다.');
        return;
      }
      const user = await currentUser();
      const verified = await client.auth.signInWithPassword({ email: user?.email, password: currentPassword });
      if (verified.error) { window.alert('현재 비밀번호가 올바르지 않습니다.'); return; }
      const { error } = await client.auth.updateUser({ password: next });
      if (error) { window.alert(error.message || '비밀번호를 변경하지 못했습니다.'); return; }
      event.currentTarget.reset();
      window.alert('비밀번호를 변경했습니다.');
    });
    document.getElementById('account-delete-button')?.addEventListener('click', () => document.getElementById('account-delete-modal')?.classList.remove('hidden'));
    document.getElementById('account-delete-cancel')?.addEventListener('click', () => document.getElementById('account-delete-modal')?.classList.add('hidden'));
    document.getElementById('account-delete-confirm')?.addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = '처리 중';
      try {
        await invoke('kakao-auth', 'delete_account');
        await client.auth.signOut({ scope: 'local' });
        window.location.replace('./');
      } catch (error) {
        window.alert(error.message || '회원 탈퇴를 처리하지 못했습니다.');
        button.disabled = false;
        button.textContent = '탈퇴하기';
      }
    });
    document.getElementById('logout-button')?.addEventListener('click', async () => {
      await client.auth.signOut();
      if (!document.getElementById('auth-screen')) window.location.replace('index.html');
    });
    document.getElementById('service-preparing-close')?.addEventListener('click', () => document.getElementById('service-preparing-modal')?.classList.add('hidden'));
    document.getElementById('service-preparing-modal')?.addEventListener('click', event => {
      if (event.target === event.currentTarget) event.currentTarget.classList.add('hidden');
    });
    await updateAdminLink();
  }

  window.MacroWatchAccountControls = Object.freeze({ bind, updateAdminLink, normalizeProfileModal });
  const start = () => bind().catch(error => console.error('account controls init failed', error));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
