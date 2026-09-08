(() => {
  'use strict';

  const selector = 'form,input,select,textarea';

  function disableAutocomplete(root = document) {
    if (root.matches?.(selector)) root.setAttribute('autocomplete', root.dataset.autocompleteToken || 'off');
    root.querySelectorAll?.(selector).forEach((element) => {
      element.setAttribute('autocomplete', element.dataset.autocompleteToken || 'off');
    });
  }

  disableAutocomplete();
  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) disableAutocomplete(node);
    }));
  }).observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener('DOMContentLoaded', () => disableAutocomplete());
})();
