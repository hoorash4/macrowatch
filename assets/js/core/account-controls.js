(() => {
  'use strict';
  const load = (src) => new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  });
  (async () => {
    await load('assets/js/core/profile-modal-normalizer.js?v=1');
    await load('assets/js/core/account-controls-main.js?v=1');
    window.MacroWatchAccountControls?.bind?.();
  })().catch(error => console.error('account controls load failed', error));
})();