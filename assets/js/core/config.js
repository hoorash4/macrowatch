window.MACROWATCH_CONFIG = Object.freeze({
  supabaseUrl: 'https://xhghpywvthjuvespzdul.supabase.co',
  supabasePublishableKey: 'sb_publishable_rPKY5Wfpp1JnSkPhIzJqJA_cijBqYgc',
  workspaceLinks: Object.freeze([
    Object.freeze({ href: 'economic-charts.html', label: '경제지표 차트', iconClass: 'fa-chart-line', target: '_self' })
  ])
});

(() => {
  const page = window.location.pathname.split('/').pop() || 'index.html';
  if (!['index.html', 'economic-charts.html'].includes(page)) return;
  if (window.MacroWatchAccountModal) return;
  document.write('<script src="assets/js/core/account-modal.js?v=2"><\/script>');
})();
