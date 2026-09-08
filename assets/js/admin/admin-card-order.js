(() => {
  const CARD_SELECTOR = ':scope > [data-admin-card-id]';
  const AUTO_SCROLL_EDGE_PX = 110;
  const AUTO_SCROLL_MAX_STEP_PX = 16;

  function create({ container, anchor, saveOrder, reportError }) {
    let draggedCard = null;
    let dragArmed = false;
    let dragPointerY = 0;
    let autoScrollFrame = null;

    const cards = () => Array.from(container.querySelectorAll(CARD_SELECTOR));
    const order = () => cards().map((card) => card.dataset.adminCardId);
    const clearDropState = () => cards().forEach((card) => {
      card.classList.remove('admin-card-drop-before', 'admin-card-drop-after');
    });
    const stopAutoScroll = () => {
      if (autoScrollFrame !== null) cancelAnimationFrame(autoScrollFrame);
      autoScrollFrame = null;
    };
    const autoScroll = () => {
      if (!draggedCard) {
        autoScrollFrame = null;
        return;
      }
      const viewportHeight = window.innerHeight;
      let step = 0;
      if (dragPointerY < AUTO_SCROLL_EDGE_PX) {
        step = -Math.ceil(AUTO_SCROLL_MAX_STEP_PX * (1 - dragPointerY / AUTO_SCROLL_EDGE_PX));
      } else if (dragPointerY > viewportHeight - AUTO_SCROLL_EDGE_PX) {
        const edgeDepth = dragPointerY - (viewportHeight - AUTO_SCROLL_EDGE_PX);
        step = Math.ceil(AUTO_SCROLL_MAX_STEP_PX * edgeDepth / AUTO_SCROLL_EDGE_PX);
      }
      if (step) window.scrollBy({ top: step, behavior: 'auto' });
      autoScrollFrame = requestAnimationFrame(autoScroll);
    };

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
        dragPointerY = event.clientY;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', card.dataset.adminCardId);
        stopAutoScroll();
        autoScrollFrame = requestAnimationFrame(autoScroll);
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
        stopAutoScroll();
        moved?.classList.remove('admin-card-dragging');
        clearDropState();
        try { await saveOrder(order()); }
        catch (error) { reportError(error); }
      });
    });
    // 기본 HTML 드래그는 화면 가장자리에서 일관되게 스크롤되지 않으므로 포인터가
    // 위·아래 가장자리에 머무는 동안 프레임 단위로 페이지를 이동한다.
    document.addEventListener('dragover', (event) => {
      if (!draggedCard) return;
      event.preventDefault();
      dragPointerY = event.clientY;
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
