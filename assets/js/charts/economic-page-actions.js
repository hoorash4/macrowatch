(() => {
  'use strict';
  const client = window.macroWatchEconomicSupabase;
  const functionClient = client && window.MacroWatchFrontend?.createFunctionClient?.(client);
  const profileButton = document.getElementById('economic-profile-button');
  const adminLink = document.getElementById('economic-admin-link');
  const logoutButton = document.getElementById('economic-logout-button');
  const modal = document.getElementById('economic-profile-modal');
  const closeButton = document.getElementById('economic-profile-close');
  const username = document.getElementById('economic-profile-username');
  const email = document.getElementById('economic-profile-email');
  const emailSave = document.getElementById('economic-profile-email-save');
  const emailRemove = document.getElementById('economic-profile-email-remove');

  async function invokeNotification(action, payload = {}) {
    if (!functionClient) throw new Error('설정 기능을 불러오지 못했습니다.');
    return functionClient.invoke('notification-settings', { action, ...payload }, { errorMessage: status => `이메일 설정 요청에 실패했습니다. (${status})` });
  }

  async function loadProfile() {
    if (!client) return;
    const { data: userData } = await client.auth.getUser();
    const user = userData.user;
    if (!user) { window.location.replace('index.html'); return; }
    const { data: account } = await client.from('user_accounts').select('username,is_admin').eq('user_id', user.id).maybeSingle();
    username.textContent = account?.username || '아이디 미등록';
    adminLink.hidden = account?.is_admin !== true;
    try {
      const settings = await invokeNotification('status');
      email.value = settings?.address || '';
      emailRemove.hidden = !settings?.is_active;
    } catch {
      email.value = '';
      emailRemove.hidden = true;
    }
  }

  profileButton?.addEventListener('click', async () => {
    modal.hidden = false;
    await loadProfile();
    closeButton?.focus();
  });
  closeButton?.addEventListener('click', () => { modal.hidden = true; profileButton?.focus(); });
  modal?.addEventListener('click', e => { if (e.target === modal || e.target?.hasAttribute?.('data-economic-profile-close')) modal.hidden = true; });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && modal && !modal.hidden) modal.hidden = true; });

  emailSave?.addEventListener('click', async () => {
    emailSave.disabled = true;
    try { await invokeNotification('save', { address: email.value.trim() }); await loadProfile(); }
    catch (error) { window.alert(error.message || '이메일 알림을 저장하지 못했습니다.'); }
    finally { emailSave.disabled = false; }
  });
  emailRemove?.addEventListener('click', async () => {
    emailRemove.disabled = true;
    try { await invokeNotification('remove'); await loadProfile(); }
    catch (error) { window.alert(error.message || '이메일 알림을 해제하지 못했습니다.'); }
    finally { emailRemove.disabled = false; }
  });

  logoutButton?.addEventListener('click', async () => {
    logoutButton.disabled = true;
    try { await client?.auth.signOut(); } finally { window.location.replace('index.html'); }
  });

  async function updateAdminAccess() {
    if (!client || !adminLink) return;
    adminLink.hidden = true;
    const { data: userData } = await client.auth.getUser();
    const userId = userData.user?.id;
    if (!userId) return;
    const { data, error } = await client.from('user_accounts').select('is_admin').eq('user_id', userId).maybeSingle();
    if (!error && data?.is_admin === true) adminLink.hidden = false;
  }
  updateAdminAccess().catch(error => console.warn('economic chart admin access check failed', error));
})();
