(() => {
  'use strict';

  const selector = 'form,input,select,textarea';

  function disableAutocomplete(root = document) {
    if (root.matches?.(selector)) root.setAttribute('autocomplete', 'off');
    root.querySelectorAll?.(selector).forEach((element) => element.setAttribute('autocomplete', 'off'));
  }

  disableAutocomplete();
  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) disableAutocomplete(node);
    }));
  }).observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener('DOMContentLoaded', () => disableAutocomplete());
})();
