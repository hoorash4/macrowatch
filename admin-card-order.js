(() => {
  const CARD_SELECTOR = ':scope > [data-admin-card-id]';

  function create({ container, anchor, saveOrder, reportError }) {
    let draggedCard = null;
    let dragArmed = false;

    const cards = () => Array.from(container.querySelectorAll(CARD_SELECTOR));
    const order = () => cards().map((card) => card.dataset.adminCardId);
    const clearDropState = () => cards().forEach((card) => {
      card.classList.remove('admin-card-drop-before', 'admin-card-drop-after');
    });

    cards().forEach((card) => {
      card.draggable = true;
      const handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'admin-card-drag-handle';
      handle.title = '카드 순서 변경';
      handle.setAttribute('aria-label', '카드 순서 변경');
      handle.innerHTML = '<i class="fa-solid fa-grip-vertical" aria-hidden="true"></i>';
      handle.addEventListener('pointerdown', () => { dragArmed = true; });
      card.append(handle);

      card.addEventListener('dragstart', (event) => {
        if (!dragArmed) {
          event.preventDefault();
          return;
        }
        draggedCard = card;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', card.dataset.adminCardId);
        requestAnimationFrame(() => card.classList.add('admin-card-dragging'));
      });
      card.addEventListener('dragover', (event) => {
        if (!draggedCard || card === draggedCard) return;
        event.preventDefault();
        clearDropState();
        const rect = card.getBoundingClientRect();
        const before = event.clientY < rect.top + rect.height / 2;
        card.classList.add(before ? 'admin-card-drop-before' : 'admin-card-drop-after');
        container.insertBefore(draggedCard, before ? card : card.nextSibling);
      });
      card.addEventListener('drop', (event) => event.preventDefault());
      card.addEventListener('dragend', async () => {
        const moved = draggedCard;
        draggedCard = null;
        dragArmed = false;
        moved?.classList.remove('admin-card-dragging');
        clearDropState();
        try { await saveOrder(order()); }
        catch (error) { reportError(error); }
      });
    });
    document.addEventListener('pointerup', () => { if (!draggedCard) dragArmed = false; });

    return {
      apply(savedOrder) {
        const byId = new Map(cards().map((card) => [card.dataset.adminCardId, card]));
        const normalized = Array.isArray(savedOrder) ? savedOrder.map(String) : [];
        normalized.forEach((id) => {
          const card = byId.get(id);
          if (!card) return;
          container.insertBefore(card, anchor);
          byId.delete(id);
        });
        // 새 카드는 저장된 기존 순서를 훼손하지 않고 기본 DOM 순서대로 뒤에 붙인다.
        byId.forEach((card) => container.insertBefore(card, anchor));
      },
      getOrder: order,
    };
  }

  window.MacroWatchAdminCardOrder = Object.freeze({ create });
})();
