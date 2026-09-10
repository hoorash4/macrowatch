(() => {
  const db = window.MacroWatchFrontend.createSupabaseClient();
  const { escapeHtml } = window.MacroWatchFrontend;
  const functionClient = window.MacroWatchFrontend.createFunctionClient(db);
  const WORKFLOW_CONTROLS = {
    backup: { button: 'run-backup-button', badge: 'backup-badge', action: 'run_backup', idleLabel: '지금 수동 백업', success: '수동 백업이 완료되었습니다.' },
    news: { button: 'run-news-button', badge: 'news-badge', action: 'run_news', idleLabel: '뉴스 분석 테스트', success: '뉴스 분석 테스트가 완료되었습니다. 결과는 저장하지 않았습니다.' },
  };
  let adminCardOrder = null;

  function formatTime(value) {
    if (!value) return '기록 없음';
    return new Intl.DateTimeFormat('ko-KR', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'Asia/Seoul'
    }).format(new Date(value));
  }


  function showNotice(title, message, isError = false) {
    document.getElementById('operation-title').textContent = title;
    document.getElementById('operation-message').textContent = message;
    const icon = document.getElementById('operation-icon');
    icon.className = isError
      ? 'fa-solid fa-circle-exclamation text-3xl text-red-400'
      : 'fa-solid fa-circle-check text-3xl text-emerald-400';
    document.getElementById('operation-modal').classList.remove('hidden');
  }

  function hideNotice() {
    document.getElementById('operation-modal').classList.add('hidden');
  }

  async function invokeAdmin(action, payload = {}) {
    return functionClient.invoke('admin-control', { ...payload, action }, {
      errorMessage: (status) => `관리자 요청에 실패했습니다. (${status})`,
    });
  }

  function initializeAdminCardOrder() {
    adminCardOrder = window.MacroWatchAdminCardOrder.create({
      container: document.getElementById('admin-management-panel'),
      anchor: document.getElementById('admin-management-anchor'),
      saveOrder: (order) => invokeAdmin('save_admin_card_order', { order }),
      reportError: (error) => showNotice('카드 순서 저장 실패', error.message || '순서를 저장하지 못했습니다.', true),
    });
  }

  function selectAdminTab(selected) {
    document.querySelectorAll('[data-admin-tab]').forEach((button) => {
      const active = button.dataset.adminTab === selected;
      button.setAttribute('aria-selected', String(active));
      button.classList.toggle('bg-blue-600', active);
      button.classList.toggle('text-white', active);
      button.classList.toggle('text-slate-400', !active);
    });
    document.querySelectorAll('[data-admin-tab-panel]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.adminTabPanel !== selected);
    });
    try { sessionStorage.setItem('macrowatch-admin-tab', selected); } catch (_) {}
  }

  function initializeAdminTabs() {
    const allowed = new Set(['management', 'information']);
    let initial = 'management';
    try {
      const saved = sessionStorage.getItem('macrowatch-admin-tab');
      if (allowed.has(saved)) initial = saved;
    } catch (_) {}
    document.querySelectorAll('[data-admin-tab]').forEach((button) => {
      button.addEventListener('click', () => selectAdminTab(button.dataset.adminTab));
    });
    selectAdminTab(initial);
  }

  async function loadAdminCardOrder() {
    const result = await invokeAdmin('get_admin_card_order');
    adminCardOrder?.apply(result.order || []);
  }

  window.MacroWatchAdminApi = Object.freeze({ invoke: invokeAdmin, notice: showNotice, setListAttentionCount });

  // 긴 관리 목록은 동일한 접기 UI를 사용한다. 목록 자체의 id는 유지해 각 기능과 분리한다.
  function initializeCollapsibleLists() {
    const labels = {
      'policy-review-list': 'FOMC 검토 목록', 'sector-etf-list': '섹터 ETF 목록',
      'extreme-news-rule-list': '결정적 뉴스 기준 목록',
      'earnings-v2-pending-list': '기업 실적 대기 목록', 'error-list': '수집 오류 목록'
    };
    document.querySelectorAll('[data-collapsible-label], #policy-review-list, #sector-etf-list, #extreme-news-rule-list, #earnings-v2-pending-list, #error-list').forEach((list) => {
      if (list.parentElement?.tagName === 'DETAILS') return;
      const details = document.createElement('details');
      details.className = 'group';
      const summary = document.createElement('summary');
      summary.className = 'mb-2 flex cursor-pointer select-none list-none items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs font-bold text-slate-300 hover:border-slate-700';
      summary.innerHTML = `<span><i class="fa-solid fa-chevron-right mr-2 transition group-open:rotate-90"></i>${escapeHtml(list.dataset.collapsibleLabel || labels[list.id] || '목록 펼치기')}</span><span data-collapsible-count class="hidden rounded-full border border-slate-700 bg-slate-900 px-2 py-0.5 text-[11px] font-semibold text-slate-400"></span>`;
      list.parentNode.insertBefore(details, list);
      details.append(summary, list);
    });
  }

  // 접힌 상태에서도 관리자가 처리할 항목이 있는 목록만 배지로 알립니다.
  function setListAttentionCount(listId, count) {
    const badge = document.getElementById(listId)?.parentElement?.querySelector('[data-collapsible-count]');
    if (!badge) return;
    const normalizedCount = Math.max(0, Number(count) || 0);
    const countClass = normalizedCount > 0 ? 'text-yellow-300' : 'text-slate-400';
    badge.innerHTML = `확인 필요 <strong class="${countClass}">${normalizedCount}</strong>건`;
    badge.classList.remove('hidden');
  }

  // 브라우저 비밀번호 관리자는 autocomplete=off를 무시할 수 있다. 관리자 자격증명
  // 입력칸은 사용자가 직접 선택할 때까지 읽기 전용으로 두고, 자동 주입값을 한 번 비운다.
  function protectCredentialInputs(root = document) {
    root.querySelectorAll('[data-admin-credential]').forEach((input) => {
      if (input.dataset.credentialProtected === 'true') return;
      input.dataset.credentialProtected = 'true';
      const activate = () => {
        if (input.dataset.clearOnActivate !== undefined && input.dataset.credentialActivated !== 'true') {
          input.value = '';
        }
        input.dataset.credentialActivated = 'true';
        input.readOnly = false;
      };
      input.addEventListener('pointerdown', activate, { once: true });
      input.addEventListener('focus', activate, { once: true });
      input.addEventListener('keydown', activate, { once: true });
    });
  }

  function renderMembers(items) {
    const list = document.getElementById('member-list');
    list.innerHTML = items.map((item) => `<article class="border-b border-slate-800 p-3 last:border-0"><form data-member-id="${escapeHtml(item.user_id)}" autocomplete="off" class="member-row-grid grid gap-2"><input name="username" value="${escapeHtml(item.username || '')}" required minlength="4" maxlength="32" autocomplete="off" placeholder="${item.username ? '아이디' : '아이디 없음 (카카오 전용)'}" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm placeholder:text-yellow-600"><input name="password" type="password" readonly data-admin-credential data-clear-on-activate data-autocomplete-token="one-time-code" autocomplete="one-time-code" placeholder="변경할 비밀번호 (선택)" class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm"><label class="flex items-center gap-2 px-2 text-xs"><input name="is_admin" type="checkbox" ${item.is_admin ? 'checked' : ''} ${item.is_current ? 'disabled' : ''} class="accent-blue-500 disabled:opacity-60">관리자</label><span class="self-center rounded-full px-2 py-1 text-center text-xs ${item.kakao_connected ? 'bg-yellow-950/50 text-yellow-400' : 'text-slate-600'}">${item.kakao_connected ? (item.username ? '카카오 연결' : '카카오 전용') : '카카오 미연결'}</span><div class="flex gap-1"><button type="submit" class="rounded-lg border border-blue-700 px-2 py-1 text-xs font-bold text-blue-300">저장</button><button type="button" data-delete-member class="rounded-lg border border-red-800 px-2 py-1 text-xs font-bold text-red-300 ${item.is_current ? 'hidden' : ''}">탈퇴</button></div></form><p class="mt-1 text-[10px] text-slate-600">가입 ${escapeHtml(formatTime(item.created_at))}${item.username ? '' : ' · ID/PW 미등록'}</p></article>`).join('') || '<p class="p-4 text-center text-sm text-slate-500">등록된 회원이 없습니다.</p>';
    protectCredentialInputs(list);
    list.querySelectorAll('[data-member-id]').forEach((form) => {
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const values = new FormData(form);
        try {
          const result = await invokeAdmin('update_member', { user_id: form.dataset.memberId, username: values.get('username'), password: values.get('password'), is_admin: values.get('is_admin') === 'on' });
          if (result.requires_reauthentication) {
            window.alert('비밀번호를 변경했습니다. 새 비밀번호로 다시 로그인해 주세요.');
            await db.auth.signOut({ scope: 'local' });
            window.location.replace('./');
            return;
          }
          showNotice('회원 저장 완료', '회원 정보를 저장했습니다.'); await loadMembers();
        } catch (error) { showNotice('회원 저장 실패', error.message || '저장하지 못했습니다.', true); }
      });
      form.querySelector('[data-delete-member]')?.addEventListener('click', async () => {
        if (!window.confirm('이 회원을 탈퇴 처리할까요? 회원 데이터도 함께 삭제됩니다.')) return;
        try { await invokeAdmin('delete_member', { user_id: form.dataset.memberId }); await loadMembers(); }
        catch (error) { showNotice('회원 탈퇴 실패', error.message || '처리하지 못했습니다.', true); }
      });
    });
  }

  async function loadMembers() {
    try { renderMembers((await invokeAdmin('list_members')).items || []); }
    catch (error) { document.getElementById('member-list').innerHTML = `<p class="p-4 text-center text-sm text-red-300">${escapeHtml(error.message || '회원 목록을 불러오지 못했습니다.')}</p>`; }
  }

  async function createMember(event) {
    event.preventDefault();
    // await 이후 Event.currentTarget은 null이 되므로 폼과 버튼을 먼저 보관한다.
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await invokeAdmin('create_member', {
        username: document.getElementById('member-username').value,
        password: document.getElementById('member-password').value,
        is_admin: document.getElementById('member-is-admin').checked
      });
      form.reset();
      form.querySelectorAll('[data-admin-credential]').forEach((input) => {
        input.value = '';
        input.readOnly = true;
        delete input.dataset.credentialActivated;
      });
      await loadMembers();
      showNotice('회원 추가 완료', '새 회원 계정을 만들었습니다.');
    } catch (error) { showNotice('회원 추가 실패', error.message || '회원을 만들지 못했습니다.', true); }
    finally { submit.disabled = false; }
  }

  function badgeState(run) {
    if (!run) return ['기록 없음', 'border-slate-700 bg-slate-800 text-slate-400'];
    if (run.status !== 'completed') return ['진행 중', 'border-blue-700/50 bg-blue-950/60 text-blue-400'];
    if (run.conclusion === 'success') return ['완료', 'border-emerald-700/50 bg-emerald-950/60 text-emerald-400'];
    if (run.conclusion === 'cancelled') return ['취소됨', 'border-amber-700/50 bg-amber-950/60 text-amber-400'];
    return ['실패', 'border-red-700/50 bg-red-950/60 text-red-400'];
  }

  function setBadge(element, run) {
    const [label, classes] = badgeState(run);
    element.className = `shrink-0 rounded-full border px-3 py-1 text-xs font-semibold ${classes}`;
    element.textContent = label;
    return label;
  }

  function applyStatus(data) {
    const checkLabel = badgeState(data.check)[0];
    document.getElementById('backend-summary').textContent = checkLabel;
    document.getElementById('backend-summary').className =
      `mt-2 text-lg font-extrabold ${data.check?.conclusion === 'success' ? 'text-emerald-400' : data.check?.status !== 'completed' ? 'text-blue-400' : 'text-amber-400'}`;
    const backupLabel = setBadge(document.getElementById('backup-badge'), data.backup);
    document.getElementById('backup-summary').textContent = backupLabel;
    document.getElementById('backup-summary').className =
      `mt-2 text-lg font-extrabold ${data.backup?.conclusion === 'success' ? 'text-emerald-400' : data.backup?.status !== 'completed' ? 'text-blue-400' : 'text-amber-400'}`;
    document.getElementById('backup-time').textContent = formatTime(data.backup?.updated_at || data.backup?.created_at);
    setBadge(document.getElementById('news-badge'), data.news);
    document.getElementById('news-time').textContent = formatTime(data.news?.updated_at || data.news?.created_at);

    applyDatabaseStatus(data.database);
  }

  function applyDatabaseStatus(database) {
    const total = Number(database?.total || 0);
    const active = Number(database?.active || 0);
    const errors = Array.isArray(database?.errors) ? database.errors : [];
    const errorCount = Number(database?.error_count || 0);
    document.getElementById('target-summary').textContent = `${active} / ${total}`;
    document.getElementById('error-summary').textContent = `${errorCount}건`;
    document.getElementById('error-summary').className =
      `mt-2 text-lg font-extrabold ${errorCount ? 'text-red-400' : 'text-emerald-400'}`;
    document.getElementById('last-db-check').textContent = `확인 ${formatTime(new Date())}`;
    renderErrors(errors);
  }

  function renderErrors(items) {
    const list = document.getElementById('error-list');
    if (!items.length) {
      list.innerHTML = '<div class="flex items-center justify-center gap-2 p-5 text-sm text-emerald-400"><i class="fa-solid fa-circle-check"></i>현재 기록된 수집 오류가 없습니다.</div>';
      return;
    }
    list.innerHTML = items.map((item) =>
      `<article class="border-b border-slate-800 p-4 last:border-0"><div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between"><div class="min-w-0"><h3 class="font-bold text-slate-200">${escapeHtml(item.title || '이름 없는 지표')}</h3><p class="mt-1 break-words text-xs leading-relaxed text-red-300">${escapeHtml(item.last_error)}</p></div><span class="shrink-0 text-[11px] text-slate-500">${formatTime(item.last_checked_at)}</span></div></article>`
    ).join('');
  }

  function renderSectorEtfs(items) {
    const list = document.getElementById('sector-etf-list');
    if (!items.length) {
      list.innerHTML = '<p class="p-4 text-center text-sm text-slate-500">등록된 섹터 ETF가 없습니다.</p>';
      return;
    }
    list.innerHTML = items.map((item) => `<article class="border-b border-slate-800 p-3 last:border-0"><div class="grid grid-cols-1 gap-2 md:grid-cols-[1fr_2fr_1fr_1fr_auto]"><input data-sector-field="sector_name" data-sector-id="${escapeHtml(item.id)}" maxlength="80" value="${escapeHtml(item.sector_name)}" class="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-100 outline-none focus:border-cyan-500"><input data-sector-field="etf_name" data-sector-id="${escapeHtml(item.id)}" maxlength="120" value="${escapeHtml(item.etf_name)}" class="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-100 outline-none focus:border-cyan-500"><input data-sector-field="etf_ticker" data-sector-id="${escapeHtml(item.id)}" maxlength="24" value="${escapeHtml(item.etf_ticker)}" class="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs uppercase text-slate-100 outline-none focus:border-cyan-500"><input data-sector-field="issuer" data-sector-id="${escapeHtml(item.id)}" maxlength="80" value="${escapeHtml(item.issuer)}" class="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-100 outline-none focus:border-cyan-500"><div class="flex gap-1"><button data-save-sector-id="${escapeHtml(item.id)}" class="rounded-lg border border-cyan-700/70 px-2 py-1.5 text-xs font-bold text-cyan-300 hover:bg-cyan-950/50">저장</button><button data-delete-sector-id="${escapeHtml(item.id)}" class="rounded-lg border border-red-700/70 px-2 py-1.5 text-xs font-bold text-red-300 hover:bg-red-950/50">삭제</button></div></div></article>`).join('');
    list.querySelectorAll('[data-save-sector-id]').forEach((button) => button.addEventListener('click', async () => {
      const id = button.dataset.saveSectorId;
      const value = (field) => list.querySelector(`[data-sector-id="${id}"][data-sector-field="${field}"]`);
      button.disabled = true;
      try {
        await invokeAdmin('save_sector_etf', {
          id,
          sector_name: value('sector_name').value,
          etf_name: value('etf_name').value,
          etf_ticker: value('etf_ticker').value,
          issuer: value('issuer').value
        });
        await loadSectorEtfs();
      } catch (error) { showNotice('섹터 ETF 저장 실패', error.message || '저장하지 못했습니다.', true); button.disabled = false; }
    }));
    list.querySelectorAll('[data-delete-sector-id]').forEach((button) => button.addEventListener('click', async () => {
      if (!window.confirm('이 섹터 ETF를 삭제할까요?')) return;
      button.disabled = true;
      try { await invokeAdmin('delete_sector_etf', { id: button.dataset.deleteSectorId }); await loadSectorEtfs(); }
      catch (error) { showNotice('섹터 ETF 삭제 실패', error.message || '삭제하지 못했습니다.', true); button.disabled = false; }
    }));
  }

  function renderExtremeNewsRules(items) {
    const list = document.getElementById('extreme-news-rule-list');
    if (!items.length) {
      list.innerHTML = '<p class="p-4 text-center text-sm text-slate-500">등록된 기준이 없습니다.</p>';
      return;
    }
    list.innerHTML = items.map((item) => `<article class="border-b border-slate-800 p-3 last:border-0"><div class="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto]"><input data-extreme-field="phrase" data-extreme-id="${escapeHtml(item.id)}" maxlength="300" value="${escapeHtml(item.phrase)}" aria-label="결정적 뉴스 기준 문장" class="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-100 outline-none focus:border-violet-500"><div class="flex gap-1"><button data-save-extreme-id="${escapeHtml(item.id)}" class="rounded-lg border border-violet-700/70 px-2 py-1.5 text-xs font-bold text-violet-300 hover:bg-violet-950/50">저장</button><button data-delete-extreme-id="${escapeHtml(item.id)}" class="rounded-lg border border-red-700/70 px-2 py-1.5 text-xs font-bold text-red-300 hover:bg-red-950/50">삭제</button></div></div></article>`).join('');
    list.querySelectorAll('[data-save-extreme-id]').forEach((button) => button.addEventListener('click', async () => {
      const id = button.dataset.saveExtremeId;
      const value = (field) => list.querySelector(`[data-extreme-id="${id}"][data-extreme-field="${field}"]`);
      button.disabled = true;
      try {
        await invokeAdmin('save_extreme_news_rule', { id, phrase: value('phrase').value });
        await loadExtremeNewsRules();
      } catch (error) { showNotice('기준 저장 실패', error.message || '저장하지 못했습니다.', true); button.disabled = false; }
    }));
    list.querySelectorAll('[data-delete-extreme-id]').forEach((button) => button.addEventListener('click', async () => {
      if (!window.confirm('이 결정적 뉴스 기준을 삭제할까요?')) return;
      button.disabled = true;
      try { await invokeAdmin('delete_extreme_news_rule', { id: button.dataset.deleteExtremeId }); await loadExtremeNewsRules(); }
      catch (error) { showNotice('기준 삭제 실패', error.message || '삭제하지 못했습니다.', true); button.disabled = false; }
    }));
  }

  async function loadExtremeNewsRules() {
    const list = document.getElementById('extreme-news-rule-list');
    try { renderExtremeNewsRules((await invokeAdmin('list_extreme_news_rules')).items || []); }
    catch (error) { list.innerHTML = '<p class="p-4 text-center text-sm text-red-300">기준 목록을 불러오지 못했습니다. DB 마이그레이션 적용 후 다시 시도해 주세요.</p>'; }
  }

  async function addExtremeNewsRule(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      await invokeAdmin('save_extreme_news_rule', {
        phrase: document.getElementById('extreme-news-phrase-input').value,
      });
      form.reset();
      await loadExtremeNewsRules();
    } catch (error) { showNotice('기준 추가 실패', error.message || '추가하지 못했습니다.', true); }
    finally { submit.disabled = false; }
  }

  async function loadSectorEtfs() {
    const list = document.getElementById('sector-etf-list');
    try { renderSectorEtfs((await invokeAdmin('list_sector_etfs')).items || []); }
    catch (error) { list.innerHTML = '<p class="p-4 text-center text-sm text-red-300">섹터 목록을 불러오지 못했습니다. DB 마이그레이션 적용 후 다시 시도해 주세요.</p>'; }
  }

  async function addSectorEtf(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const result = await invokeAdmin('save_sector_etf', {
        sector_name: document.getElementById('sector-name-input').value,
        etf_ticker: document.getElementById('sector-etf-ticker-input').value
      });
      form.reset();
      await loadSectorEtfs();
      const historyMessage = result.history_backfill_pending
        ? '가격 이력은 다음 정기 수집에서 자동으로 보완됩니다.'
        : `최근 가격 ${Number(result.price_rows || 0).toLocaleString('ko-KR')}건도 함께 등록했습니다.`;
      showNotice('섹터 ETF 등록 완료', `${result.item?.etf_name || 'ETF'}을(를) 등록했습니다. ${historyMessage}`);
    } catch (error) { showNotice('섹터 ETF 추가 실패', error.message || '추가하지 못했습니다.', true); }
    finally { submit.disabled = false; }
  }

  function renderEarningsV2Pending(items) {
    const list = document.getElementById('earnings-v2-pending-list');
    setListAttentionCount('earnings-v2-pending-list', items.length);
    if (!items.length) {
      list.innerHTML = '<p class="p-4 text-center text-sm text-emerald-400">대기 중인 기업 실적이 없습니다.</p>';
      return;
    }
    const missingLabel = (item) => [
      item.missing_top_line ? '매출' : '', item.missing_operating_income ? '영업이익' : '', item.missing_net_income ? '순이익' : '',
    ].filter(Boolean).join(' · ');
    const amountValue = (value) => value == null ? '' : String(value);
    list.innerHTML = items.map((item) => `<article class="border-b border-slate-800 p-4 last:border-0"><form class="earnings-v2-pending-form space-y-3" data-company-id="${escapeHtml(item.company_id)}" data-fiscal-year="${Number(item.market_year)}" data-fiscal-quarter="${Number(item.market_quarter)}"><div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><div><p class="font-bold text-slate-200">${escapeHtml(item.company_name)} <span class="ml-1 text-xs font-normal text-slate-500">${escapeHtml(item.stock_code || '')}</span></p><p class="mt-1 text-xs text-slate-500">${escapeHtml(`${item.market_year} Q${item.market_quarter}`)} · ${escapeHtml(item.market_id)}</p></div><span class="w-fit rounded-full border border-orange-800/70 bg-orange-950/40 px-2.5 py-1 text-xs font-bold text-orange-300">미확보: ${escapeHtml(missingLabel(item))}</span></div><div class="grid gap-2 md:grid-cols-[1fr_1fr_1fr_auto]"><label class="text-xs text-slate-400">매출<input name="top_line" autocomplete="off" inputmode="decimal" required value="${escapeHtml(amountValue(item.top_line))}" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"></label><label class="text-xs text-slate-400">영업이익<input name="operating_income" autocomplete="off" inputmode="decimal" required value="${escapeHtml(amountValue(item.operating_income))}" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"></label><label class="text-xs text-slate-400">순이익<input name="net_income" autocomplete="off" inputmode="decimal" required value="${escapeHtml(amountValue(item.net_income))}" class="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-blue-500"></label><button type="submit" class="self-end rounded-lg border border-blue-600 px-4 py-2 text-sm font-bold text-blue-300 hover:bg-blue-950/40">확정</button></div></form></article>`).join('');
    list.querySelectorAll('.earnings-v2-pending-form').forEach((form) => form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submit = form.querySelector('button[type="submit"]');
      const values = new FormData(form);
      submit.disabled = true;
      try {
        const result = await invokeAdmin('resolve_earnings_v2_pending', {
          company_id: form.dataset.companyId,
          fiscal_year: Number(form.dataset.fiscalYear),
          fiscal_quarter: Number(form.dataset.fiscalQuarter),
          top_line: values.get('top_line'),
          operating_income: values.get('operating_income'),
          net_income: values.get('net_income'),
        });
        await loadEarningsV2Pending();
        showNotice('기업 실적 확정 완료', result.recalculation_dispatched ? '수동 값을 저장했고 해당 분기 재계산을 시작했습니다.' : '수동 값은 저장했습니다. 분기 재계산은 다음 수집 때 반영됩니다.');
      } catch (error) {
        showNotice('기업 실적 확정 실패', error.message || '수동 값을 저장하지 못했습니다.', true);
      } finally { submit.disabled = false; }
    }));
  }

  async function loadEarningsV2Pending() {
    try { renderEarningsV2Pending((await invokeAdmin('list_earnings_v2_pending')).items || []); }
    catch (error) {
      setListAttentionCount('earnings-v2-pending-list', 0);
      document.getElementById('earnings-v2-pending-list').innerHTML = '<p class="p-4 text-center text-sm text-red-300">기업 실적 대기 목록을 불러오지 못했습니다.</p>';
    }
  }

  function confirmAutomationDeletion(message) {
    return new Promise((resolve) => {
      const modal = document.createElement('div');
      modal.className = 'fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4';
      modal.innerHTML = `<section class="w-full max-w-sm rounded-2xl border border-red-800 bg-slate-900 p-6 text-center shadow-2xl"><i class="fa-solid fa-triangle-exclamation text-3xl text-red-400"></i><h2 class="mt-4 text-lg font-bold text-white">자동수집 삭제 확인</h2><p class="mt-2 text-sm leading-relaxed text-slate-400">${escapeHtml(message)}</p><div class="mt-5 grid grid-cols-2 gap-2"><button type="button" data-cancel class="rounded-lg border border-slate-700 py-2.5 text-sm font-bold text-slate-300">취소</button><button type="button" data-confirm class="rounded-lg bg-red-600 py-2.5 text-sm font-bold text-white hover:bg-red-500">삭제</button></div></section>`;
      const close = (confirmed) => { modal.remove(); resolve(confirmed); };
      modal.querySelector('[data-cancel]').addEventListener('click', () => close(false));
      modal.querySelector('[data-confirm]').addEventListener('click', () => close(true));
      modal.addEventListener('click', (event) => { if (event.target === modal) close(false); });
      document.body.append(modal);
    });
  }

  function renderAutomationSchedules(items) {
    const list = document.getElementById('automation-schedule-list');
    if (!items.length) { list.innerHTML = '<p class="p-4 text-center text-sm text-slate-500">등록된 정기 자동수집이 없습니다.</p>'; return; }
    list.innerHTML = items.map((item) => {
      const paused = item.state !== 'active';
      return `<article data-automation-workflow-id="${escapeHtml(item.workflow_id)}" data-automation-cron="${escapeHtml(item.cron)}" data-automation-name="${escapeHtml(item.name)}" data-automation-paused="${paused}" class="border-b border-slate-800 p-4 last:border-0"><div class="grid gap-3 lg:grid-cols-[minmax(260px,1fr)_8rem_auto]"><div class="min-w-0"><h3 class="truncate font-bold text-slate-200">${escapeHtml(item.name)}</h3><p class="mt-1 text-[11px] text-slate-500">최근 성공 ${escapeHtml(formatTime(item.latest_success?.updated_at))}</p></div><input data-automation-time type="time" value="${escapeHtml(item.kst_time || '')}" class="w-32 min-w-32 rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-white"><div class="flex flex-wrap gap-1"><button type="button" data-save-automation class="rounded-lg border border-blue-700 px-2.5 py-1.5 text-xs font-bold text-blue-300">시간 저장</button><button type="button" data-toggle-automation title="${paused ? '클릭하여 재시작' : '클릭하여 중지'}" class="rounded-lg border px-2.5 py-1.5 text-xs font-bold ${paused ? 'border-amber-800 text-amber-300' : 'border-emerald-800 text-emerald-300'}">${paused ? '중지됨' : '실행 중'}</button><button type="button" data-delete-automation-time title="이 실행 삭제" class="rounded-lg border border-red-800 px-2.5 py-1.5 text-xs font-bold text-red-300 hover:bg-red-950/50">삭제</button></div></div></article>`;
    }).join('');
    list.querySelectorAll('[data-automation-workflow-id]').forEach((row) => {
      const id = row.dataset.automationWorkflowId, cron = row.dataset.automationCron, name = row.dataset.automationName;
      row.querySelector('[data-save-automation]').addEventListener('click', async (event) => {
        const button = event.currentTarget; button.disabled = true;
        try { await invokeAdmin('update_automation_time', { workflow_id: id, cron, time: row.querySelector('[data-automation-time]').value }); await loadAutomationSchedules(); }
        catch (error) { showNotice('일정 저장 실패', error.message || '시간을 저장하지 못했습니다.', true); button.disabled = false; }
      });
      row.querySelector('[data-toggle-automation]').addEventListener('click', async (event) => {
        const button = event.currentTarget; button.disabled = true;
        try { await invokeAdmin('set_automation_enabled', { workflow_id: id, enabled: row.dataset.automationPaused === 'true' }); await loadAutomationSchedules(); }
        catch (error) { showNotice('상태 변경 실패', error.message || '상태를 바꾸지 못했습니다.', true); button.disabled = false; }
      });
      row.querySelector('[data-delete-automation-time]').addEventListener('click', async () => {
        const input = row.querySelector('[data-automation-time]');
        if (!await confirmAutomationDeletion(`${name}의 ${input.value} 실행을 삭제합니다. 마지막 실행이면 해당 자동수집 전체가 삭제됩니다.`)) return;
        try { await invokeAdmin('delete_automation_time', { workflow_id: id, cron }); await loadAutomationSchedules(); }
        catch (error) { showNotice('실행 시간 삭제 실패', error.message || '삭제하지 못했습니다.', true); }
      });
    });
  }

  async function loadAutomationSchedules() {
    const list = document.getElementById('automation-schedule-list');
    try { renderAutomationSchedules((await invokeAdmin('list_automation_schedules')).items || []); }
    catch (error) { list.innerHTML = `<p class="p-4 text-center text-sm text-red-300">${escapeHtml(error.message || '자동 수집 목록을 불러오지 못했습니다.')}</p>`; }
  }

  async function loadAll() {
    const button = document.getElementById('refresh-button');
    button.disabled = true;
    button.classList.add('opacity-60');
    try {
      const status = await invokeAdmin('status');
      applyStatus(status);
      await loadEarningsV2Pending();
      await loadSectorEtfs();
      await loadExtremeNewsRules();
      await loadMembers();
      await loadAutomationSchedules();
    } catch (error) {
      showNotice('불러오기 실패', error.message || '상태를 확인하지 못했습니다.', true);
    } finally {
      button.disabled = false;
      button.classList.remove('opacity-60');
    }
  }

  async function waitForCompletion(kind, requestedAt) {
    const control = WORKFLOW_CONTROLS[kind] || WORKFLOW_CONTROLS.news;
    const requested = new Date(requestedAt).getTime() - 5000;
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5000));
      const { run } = await invokeAdmin('workflow_status', { kind });
      setBadge(document.getElementById(control.badge), run);
      if (run && new Date(run.created_at).getTime() >= requested && run.status === 'completed') {
        return run;
      }
    }
    throw new Error('실행 완료 확인 시간이 초과되었습니다. 새로고침해서 상태를 확인해 주세요.');
  }

  async function runWorkflow(kind) {
    const control = WORKFLOW_CONTROLS[kind] || WORKFLOW_CONTROLS.news;
    const button = document.getElementById(control.button);
    const label = button.querySelector('span');
    button.disabled = true;
    label.textContent = '진행 중';
    setBadge(document.getElementById(control.badge), {
      status: 'queued'
    });
    try {
      const result = await invokeAdmin(control.action);
      const run = await waitForCompletion(kind, result.requested_at);
      if (run.conclusion === 'success') {
        showNotice('실행 완료', control.success);
      } else {
        showNotice('실행 실패', `작업이 ${run.conclusion || '실패'} 상태로 종료되었습니다.`, true);
      }
    } catch (error) {
      showNotice('실행 실패', error.message || '작업을 실행하지 못했습니다.', true);
    } finally {
      button.disabled = false;
      label.textContent = control.idleLabel;
      await loadAll();
    }
  }

  async function authorizeAdmin() {
    const accessScreen = document.getElementById('admin-access-screen');
    const accessMessage = document.getElementById('admin-access-message');
    const backLink = document.getElementById('admin-access-back');
    try {
      if (!db) throw new Error('로그인 기능을 불러오지 못했습니다.');
      const { data: sessionData } = await db.auth.getSession();
      const userId = sessionData.session?.user?.id;
      if (!userId) {
        window.location.replace('./');
        return;
      }
      const { data, error } = await db
        .from('user_accounts')
        .select('is_admin')
        .eq('user_id', userId)
        .maybeSingle();
      if (error) throw error;
      if (data?.is_admin !== true) {
        accessMessage.textContent = '관리자만 접근할 수 있는 페이지입니다.';
        backLink.classList.remove('hidden');
        backLink.classList.add('flex');
        return;
      }
      accessScreen.classList.add('hidden');
      document.getElementById('admin-shell').classList.remove('hidden');
      await loadAdminCardOrder();
      await loadAll();
      window.dispatchEvent(new CustomEvent('macrowatch:admin-ready'));
    } catch (error) {
      accessMessage.textContent = error.message || '관리자 권한을 확인하지 못했습니다.';
      backLink.classList.remove('hidden');
      backLink.classList.add('flex');
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    initializeAdminTabs();
    initializeAdminCardOrder();
    initializeCollapsibleLists();
    protectCredentialInputs();
    document.getElementById('refresh-button').addEventListener('click', loadAll);
    Object.entries(WORKFLOW_CONTROLS).forEach(([kind, control]) => {
      document.getElementById(control.button).addEventListener('click', () => runWorkflow(kind));
    });
    document.getElementById('refresh-earnings-pending-button').addEventListener('click', loadEarningsV2Pending);
    document.getElementById('refresh-automation-schedules-button').addEventListener('click', loadAutomationSchedules);
    document.getElementById('sector-etf-form').addEventListener('submit', addSectorEtf);
    document.getElementById('extreme-news-rule-form').addEventListener('submit', addExtremeNewsRule);
    document.getElementById('member-form').addEventListener('submit', createMember);
    document.getElementById('operation-close').addEventListener('click', hideNotice);
    document.getElementById('operation-modal').addEventListener('click', (event) => {
      if (event.target === event.currentTarget) hideNotice();
    });
    authorizeAdmin();
  });
})();

