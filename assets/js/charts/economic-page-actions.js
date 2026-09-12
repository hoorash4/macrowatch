(() => {
  'use strict';
  const client = window.MacroWatchFrontend?.createSupabaseClient?.();
  const profileButton = document.getElementById('economic-profile-button');
  const adminLink = document.getElementById('economic-admin-link');
  const logoutButton = document.getElementById('economic-logout-button');

  profileButton?.addEventListener('click', () => {
    window.sessionStorage.setItem('macrowatch.open-profile', '1');
    window.location.href = 'index.html';
  });

  logoutButton?.addEventListener('click', async () => {
    logoutButton.disabled = true;
    try { await client?.auth.signOut(); }
    finally { window.location.replace('index.html'); }
  });

  async function updateAdminAccess() {
    if (!client || !adminLink) return;
    adminLink.hidden = true;
    const { data: sessionData } = await client.auth.getSession();
    const userId = sessionData.session?.user?.id;
    if (!userId) return;
    const { data, error } = await client.from('user_accounts').select('is_admin').eq('user_id', userId).maybeSingle();
    if (!error && data?.is_admin === true) adminLink.hidden = false;
  }

  updateAdminAccess().catch((error) => console.warn('economic chart admin access check failed', error));
})();
