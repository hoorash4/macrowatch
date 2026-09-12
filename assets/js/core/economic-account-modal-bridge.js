(() => {
  'use strict';

  document.addEventListener('DOMContentLoaded', async () => {
    const button = document.getElementById('profile-button');
    const client = window.macroWatchSupabase || window.MacroWatchFrontend?.createSupabaseClient?.();
    if (!button || !client) return;

    let userId = null;
    try {
      const { data, error } = await client.auth.getSession();
      if (error) throw error;
      userId = data?.session?.user?.id || null;
    } catch (error) {
      console.error('chart profile session read failed', error);
    }

    button.addEventListener('click', event => {
      if (!userId) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      window.MacroWatchAccountModal?.open?.({ client, userId })
        .catch(error => console.error('chart profile open failed', error));
    }, true);
  });
})();
