(() => {
  'use strict';

  const client = window.macroWatchSupabase || window.MacroWatchFrontend.createSupabaseClient();
  window.macroWatchSupabase = client;

  async function session() {
    const { data, error } = await client.auth.getSession();
    if (error) throw error;
    return data.session;
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

    window.MacroWatchAccountModal.ensure();
    window.MacroWatchAccountModal.bindTrigger({
      button: document.getElementById('profile-button'),
      client,
      deleteRedirect: 'index.html',
    });

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
