(() => {
  'use strict';
  const original = window.supabase?.createClient;
  if (!original || original.__macroWatchEconomicCapture) return;
  function capture(...args) {
    const client = original.apply(window.supabase, args);
    window.macroWatchEconomicSupabase = client;
    window.supabase.createClient = original;
    return client;
  }
  capture.__macroWatchEconomicCapture = true;
  window.supabase.createClient = capture;
})();
