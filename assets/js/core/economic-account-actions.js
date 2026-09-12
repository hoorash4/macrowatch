(() => {
  'use strict';

  const client = window.macroWatchEconomicSupabase;
  const profileButton = document.getElementById('economic-profile-button');
  const adminLink = document.getElementById('economic-admin-link');
  const logoutButton = document.getElementById('economic-logout-button');
  const functionClient = client && window.MacroWatchFrontend?.createFunctionClient?.(client);

  async function currentUser() {
    if (!client) return null;
    const { data, error } = await client.auth.getSession();
    if (error) throw error;
    return data.session?.user || null;
  }

  function pinProfileModalColors() {
    const fixed = {
      '--theme-surface': '#0f172a',
      '--theme-surface-elevated': '#0f172a',
      '--theme-text': '#e2e8f0',
      '--theme-text-secondary': '#94a3b8',
      '--theme-text-muted': '#64748b',
      '--theme-border': '#334155',
      '--theme-input-bg': '#0f172a',
      '--theme-input-border': '#334155',
    };
    for (const id of ['profile-modal', 'account-delete-modal']) {
      const modal = document.getElementById(id);
      if (!modal) continue;
      modal.style.colorScheme = 'dark';
      for (const [name, value] of Object.entries(fixed)) modal.style.setProperty(name, value);
    }
  }

  function ensureThemePreferenceControl() {
    if (document.getElementById('theme-preference')) return;
    const body = document.querySelector('#profile-modal .profile-dialog-body');
    if (!body) return;
    const card = document.createElement('section');
    card.className = 'theme-preference-card';
    card.style.background = 'rgba(2,6,23,.6)';
    card.style.borderColor = '#1e293b';
    card.innerHTML = `
      <label for="theme-preference" style="color:#e2e8f0">화면 테마</label>
      <select id="theme-preference" aria-label="화면 테마" style="background:#0f172a;border-color:#334155;color:#e2e8f0">
        <option value="system">시스템 설정</option>
        <option value="light">라이트 모드</option>
        <option value="dark">다크 모드</option>
      </select>
      <p style="color:#64748b">시스템 설정은 기기의 라이트/다크 모드 변경을 실시간으로 따릅니다.</p>`;
    const deleteSection = document.getElementById('account-delete-button')?.closest('.rounded-xl');
    if (deleteSection?.parentElement === body) body.insertBefore(card, deleteSection);
    else body.append(card);
    const select = card.querySelector('#theme-preference');
    select.value = window.MacroWatchTheme?.getPreference?.() || 'system';
    select.addEventListener('change', async () => {
      select.disabled = true;
      try { await window.MacroWatchTheme?.savePreference?.(select.value); }
      catch (error) { window.alert(error?.message || '테마 설정을 저장하지 못했습니다.'); }
      finally { select.disabled = false; }
    });
  }

  async function importExistingProfileModals() {
    if (document.getElementById('profile-modal')) {
      pinProfileModalColors();
      ensureThemePreferenceControl();
      return;
    }
    const response = await fetch('index.html', { cache: 'no-cache' });
    if (!response.ok) throw new Error(`개인 설정 화면을 불러오지 못했습니다. (${response.status})`);
    const source = new DOMParser().parseFromString(await response.text(), 'text/html');
    const profile = source.getElementById('profile-modal');
    const accountDelete = source.getElementById('account-delete-modal');
    if (!profile || !accountDelete) throw new Error('기존 개인 설정 화면을 찾지 못했습니다.');
    document.body.append(document.importNode(profile, true), document.importNode(accountDelete, true));
    pinProfileModalColors();
    ensureThemePreferenceControl();
    window.MacroWatchTheme?.loadStoredPreference?.();
  }

  function elements() {
    return {
      modal: document.getElementById('profile-modal'),
      close: document.getElementById('profile-close-button'),
      username: document.getElementById('profile-username'),
      kakaoStatus: document.getElementById('kakao-connection-status'),
      kakaoBadge: document.getElementById('kakao-status-badge'),
      kakaoConnect: document.getElementById('kakao-connect-button'),
      kakaoUnlink: document.getElementById('kakao-unlink-button'),
      emailStatus: document.getElementById('email-alert-status'),
      emailBadge: document.getElementById('email-alert-badge'),
      emailAddress: document.getElementById('email-alert-address'),
      emailSave: document.getElementById('email-alert-save-button'),
      emailRemove: document.getElementById('email-alert-remove-button'),
      passwordForm: document.getElementById('password-change-form'),
      deleteButton: document.getElementById('account-delete-button'),
      deleteModal: document.getElementById('account-delete-modal'),
      deleteCancel: document.getElementById('account-delete-cancel'),
      deleteConfirm: document.getElementById('account-delete-confirm'),
    };
  }

  async function invoke(functionName, action, payload = {}) {
    if (!functionClient) throw new Error('설정 기능을 불러오지 못했습니다.');
    return functionClient.invoke(functionName, { action, ...payload }, {
      errorMessage: status => `${functionName} 요청에 실패했습니다. (${status})`,
    });
  }

  function setKakaoStatus(el, connected, message) {
    el.kakaoStatus.textContent = message || (connected ? '로그인 한 카카오 계정으로 지표 변동 알림이 전송 됩니다.' : '카카오 알림 연결을 확인하지 못했습니다.');
    el.kakaoBadge.textContent = connected ? '연결됨' : '확인 필요';
    el.kakaoBadge.className = connected
      ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
      : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    el.kakaoUnlink.classList.toggle('hidden', !connected);
  }

  function setEmailStatus(el, address, active, message) {
    el.emailStatus.textContent = message || (active ? `${address}로 지표 변동 알림을 받습니다.` : '이메일 알림을 설정하지 않았습니다.');
    el.emailBadge.textContent = active ? '사용 중' : '미설정';
    el.emailBadge.className = active
      ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
      : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
    el.emailAddress.value = address || '';
    el.emailRemove.classList.toggle('hidden', !active);
  }

  async function loadProfile(el) {
    const user = await currentUser();
    if (!user) throw new Error('로그인이 필요합니다.');
    const [{ data: account, error: accountError }, kakao, email] = await Promise.all([
      client.from('user_accounts').select('username').eq('user_id', user.id).maybeSingle(),
      invoke('kakao-auth', 'status').catch(error => ({ error })),
      invoke('notification-settings', 'status').catch(error => ({ error })),
    ]);
    el.username.textContent = accountError ? '아이디를 확인하지 못했습니다.' : (account?.username || '아이디 미등록 · 카카오 전용 계정');
    if (kakao?.error) setKakaoStatus(el, false, kakao.error.message || '연결 상태를 확인하지 못했습니다.');
    else setKakaoStatus(el, Boolean(kakao?.connected));
    if (email?.error) setEmailStatus(el, '', false, email.error.message || '이메일 설정을 확인하지 못했습니다.');
    else setEmailStatus(el, email?.address || '', Boolean(email?.is_active));
  }

  function bindProfileEvents(el) {
    if (el.modal.dataset.economicBound === 'true') return;
    el.modal.dataset.economicBound = 'true';
    const close = () => { el.modal.classList.add('hidden'); profileButton?.focus(); };
    el.close?.addEventListener('click', close);
    el.modal.addEventListener('click', event => { if (event.target === el.modal) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !el.modal.classList.contains('hidden')) close(); });

    el.kakaoConnect?.addEventListener('click', () => window.alert('카카오 계정 다시 연결 기능은 현재 준비 중입니다.'));
    el.kakaoUnlink?.addEventListener('click', async () => {
      if (!window.confirm('카카오 알림 연결을 해제할까요? ID 계정은 유지됩니다.')) return;
      el.kakaoUnlink.disabled = true;
      try { await invoke('kakao-auth', 'unlink'); await loadProfile(el); }
      catch (error) { window.alert(error.message || '카카오 연결을 해제하지 못했습니다.'); }
      finally { el.kakaoUnlink.disabled = false; }
    });
    el.emailSave?.addEventListener('click', async () => {
      el.emailSave.disabled = true;
      try { await invoke('notification-settings', 'save', { address: el.emailAddress.value.trim() }); await loadProfile(el); }
      catch (error) { window.alert(error.message || '이메일 알림을 저장하지 못했습니다.'); }
      finally { el.emailSave.disabled = false; }
    });
    el.emailRemove?.addEventListener('click', async () => {
      if (!window.confirm('이메일 알림을 해제할까요?')) return;
      el.emailRemove.disabled = true;
      try { await invoke('notification-settings', 'remove'); await loadProfile(el); }
      catch (error) { window.alert(error.message || '이메일 알림을 해제하지 못했습니다.'); }
      finally { el.emailRemove.disabled = false; }
    });
    el.passwordForm?.addEventListener('submit', async event => {
      event.preventDefault();
      const current = document.getElementById('current-password').value;
      const next = document.getElementById('new-password').value;
      const confirm = document.getElementById('confirm-password').value;
      if (next.length < 6 || next !== confirm) {
        window.alert(next.length < 6 ? '새 비밀번호는 6자 이상이어야 합니다.' : '새 비밀번호가 서로 다릅니다.');
        return;
      }
      const user = await currentUser();
      if (!user?.email) return;
      const verified = await client.auth.signInWithPassword({ email: user.email, password: current });
      if (verified.error) { window.alert('현재 비밀번호가 올바르지 않습니다.'); return; }
      const { error } = await client.auth.updateUser({ password: next });
      if (error) { window.alert(error.message || '비밀번호를 변경하지 못했습니다.'); return; }
      event.currentTarget.reset();
      window.alert('비밀번호를 변경했습니다.');
    });
    el.deleteButton?.addEventListener('click', () => el.deleteModal.classList.remove('hidden'));
    el.deleteCancel?.addEventListener('click', () => el.deleteModal.classList.add('hidden'));
    el.deleteConfirm?.addEventListener('click', async () => {
      el.deleteConfirm.disabled = true;
      try {
        await invoke('kakao-auth', 'delete_account');
        await client.auth.signOut({ scope: 'local' });
        window.location.replace('index.html');
      } catch (error) {
        window.alert(error.message || '회원 탈퇴를 처리하지 못했습니다.');
        el.deleteConfirm.disabled = false;
      }
    });
  }

  async function showProfile() {
    await importExistingProfileModals();
    const el = elements();
    bindProfileEvents(el);
    el.modal.classList.remove('hidden');
    el.close?.focus();
    await loadProfile(el);
  }

  async function updateAdminAccess() {
    if (!client || !adminLink) return;
    adminLink.hidden = true;
    const user = await currentUser();
    if (!user) return;
    const { data, error } = await client.from('user_accounts').select('is_admin').eq('user_id', user.id).maybeSingle();
    if (!error && data?.is_admin === true) adminLink.hidden = false;
  }

  profileButton?.addEventListener('click', () => showProfile().catch(error => window.alert(error.message || '개인 설정을 열지 못했습니다.')));
  logoutButton?.addEventListener('click', async () => {
    logoutButton.disabled = true;
    try { await client.auth.signOut({ scope: 'local' }); }
    finally { window.location.replace('index.html'); }
  });
  updateAdminAccess().catch(error => console.warn('economic chart admin access check failed', error));
})();
