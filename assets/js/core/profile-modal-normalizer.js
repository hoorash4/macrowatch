(() => {
  'use strict';
  function normalize() {
    const modal = document.getElementById('profile-modal');
    const deleteModal = document.getElementById('account-delete-modal');
    const body = modal?.querySelector('.profile-dialog-body');
    const themeCard = document.getElementById('theme-preference')?.closest('.theme-preference-card');
    const deleteSection = document.getElementById('account-delete-button')?.closest('.rounded-xl');
    if (body && themeCard && deleteSection?.parentElement === body && themeCard.nextElementSibling !== deleteSection) body.insertBefore(themeCard, deleteSection);
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
  }
  document.addEventListener('DOMContentLoaded', normalize);
  window.addEventListener('macrowatch:themechange', normalize);
})();
