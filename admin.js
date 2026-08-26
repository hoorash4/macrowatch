(() => {
  const db = window.MacroWatchFrontend.createSupabaseClient();
  const { escapeHtml } = window.MacroWatchFrontend;
  const functionClient = window.MacroWatchFrontend.createFunctionClient(db);
  let scheduleTimes = ['08:00', '18:00'];

  function defaultScheduleTime(index) {
    return ['08:00', '12:00', '16:00', '20:00'][index] || '08:00';
  }

  function renderScheduleTimeInputs(times = scheduleTimes) {
    const count = Math.min(4, Math.max(1, Number(document.getElementById('schedule-count').value) || 1));
    const container = document.getElementById('schedule-times');
    container.innerHTML = Array.from({ length: count }, (_, index) => `
      <input data-schedule-time type="time" value="${escapeHtml(times[index] || defaultScheduleTime(index))}" aria-label="${index + 1}회차 확인 시간" class="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-2 py-2 text-sm text-white outline-none focus:border-blue-500">
    `).join('');
  }

  function formatTime(value) {
    if (!value) return '기록 없음';
    return new Intl.DateTimeFormat('ko-KR', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'Asia/Seoul'
    }).format(new Date(value));
  }

  function formatDuration(start, end) {
    if (!start || !end) return '소요 시간 확인 불가';
    const seconds = Math.max(0, Math.round((new Date(end) - new Date(start)) / 1000));
    return `약 ${seconds}초 소요`;
  }

  function nextScheduledCheck(times) {
    const now = new Date();
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Seoul',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23'
    }).formatToParts(now).reduce((result, part) => ({ ...result, [part.type]: part.value }), {});
    const current = `${parts.hour}:${parts.minute}`;
    const sorted = [...times].sort();
    const todayTime = sorted.find((time) => time > current);
    if (todayTime) return formatTime(new Date(`${parts.year}-${parts.month}-${parts.day}T${todayTime}:00+09:00`));
    const tomorrow = new Date(`${parts.year}-${parts.month}-${parts.day}T00:00:00+09:00`);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const nextDay = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Seoul',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).format(tomorrow);
    return formatTime(new Date(`${nextDay}T${sorted[0]}:00+09:00`));
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

  window.MacroWatchAdminApi = Object.freeze({ invoke: invokeAdmin, notice: showNotice });

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
    const checkLabel = setBadge(document.getElementById('check-badge'), data.check);
    document.getElementById('backend-summary').textContent = checkLabel;
    document.getElementById('backend-summary').className =
      `mt-2 text-lg font-extrabold ${data.check?.conclusion === 'success' ? 'text-emerald-400' : data.check?.status !== 'completed' ? 'text-blue-400' : 'text-amber-400'}`;
    document.getElementById('check-time').textContent = formatTime(data.check?.updated_at || data.check?.created_at);
    document.getElementById('check-duration').textContent = formatDuration(
      data.check?.run_started_at || data.check?.created_at,
      data.check?.updated_at
    );

    const backupLabel = setBadge(document.getElementById('backup-badge'), data.backup);
    document.getElementById('backup-summary').textContent = backupLabel;
    document.getElementById('backup-summary').className =
      `mt-2 text-lg font-extrabold ${data.backup?.conclusion === 'success' ? 'text-emerald-400' : data.backup?.status !== 'completed' ? 'text-blue-400' : 'text-amber-400'}`;
    document.getElementById('backup-time').textContent = formatTime(data.backup?.updated_at || data.backup?.created_at);
    setBadge(document.getElementById('news-badge'), data.news);
    document.getElementById('news-time').textContent = formatTime(data.news?.updated_at || data.news?.created_at);

    scheduleTimes = Array.isArray(data.schedule?.times) && data.schedule.times.length ? data.schedule.times : ['08:00', '18:00'];
    document.getElementById('schedule-count').value = String(scheduleTimes.length);
    renderScheduleTimeInputs(scheduleTimes);
    document.getElementById('schedule-label').textContent = `매일 ${scheduleTimes.join(' · ')}`;
    document.getElementById('next-check-time').textContent = nextScheduledCheck(scheduleTimes);
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

  function renderUncertainNews(items) {
    const list = document.getElementById('uncertain-news-list');
    if (!items.length) {
      list.innerHTML = '<p class="p-4 text-center text-sm text-emerald-400">검토할 불명확 뉴스가 없습니다.</p>';
      return;
    }
    list.innerHTML = items.map((item) => `<article class="border-b border-slate-800 p-4 last:border-0"><div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div class="min-w-0"><p class="text-xs text-slate-500">${escapeHtml(formatTime(item.published_at))} · ${escapeHtml(item.source_name)}</p><p class="mt-2 text-sm leading-relaxed text-slate-200">${escapeHtml(item.uncertain_summary || '시장 방향을 판단하기 어렵습니다.')}</p><div class="mt-2 flex flex-wrap gap-1">${(item.derived_keywords || []).map((keyword) => `<span class="rounded-full border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400">${escapeHtml(keyword)}</span>`).join('')}</div></div><div class="flex shrink-0 flex-wrap gap-2"><button data-article-id="${escapeHtml(item.id)}" data-sentiment="positive" class="resolve-news rounded-lg border border-emerald-700/60 px-2 py-1 text-xs font-bold text-emerald-300">긍정</button><button data-article-id="${escapeHtml(item.id)}" data-sentiment="negative" class="resolve-news rounded-lg border border-red-700/60 px-2 py-1 text-xs font-bold text-red-300">부정</button><button data-article-id="${escapeHtml(item.id)}" data-sentiment="neutral" class="resolve-news rounded-lg border border-slate-600 px-2 py-1 text-xs font-bold text-slate-300">중립</button><button data-article-id="${escapeHtml(item.id)}" class="exclude-news rounded-lg border border-amber-700/60 px-2 py-1 text-xs font-bold text-amber-300">제외</button></div></div></article>`).join('');
    list.querySelectorAll('.resolve-news').forEach((button) => button.addEventListener('click', async () => {
      button.disabled = true;
      try { await invokeAdmin('resolve_uncertain_news', { article_id: button.dataset.articleId, sentiment: button.dataset.sentiment }); await loadUncertainNews(); }
      catch (error) { showNotice('분류 저장 실패', error.message || '처리하지 못했습니다.', true); button.disabled = false; }
    }));
    list.querySelectorAll('.exclude-news').forEach((button) => button.addEventListener('click', async () => {
      button.disabled = true;
      try { await invokeAdmin('exclude_uncertain_news', { article_id: button.dataset.articleId }); await loadUncertainNews(); }
      catch (error) { showNotice('제외 처리 실패', error.message || '처리하지 못했습니다.', true); button.disabled = false; }
    }));
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
      await invokeAdmin('save_sector_etf', {
        sector_name: document.getElementById('sector-name-input').value,
        etf_ticker: document.getElementById('sector-etf-ticker-input').value
      });
      form.reset();
      await loadSectorEtfs();
    } catch (error) { showNotice('섹터 ETF 추가 실패', error.message || '추가하지 못했습니다.', true); }
    finally { submit.disabled = false; }
  }

  async function loadUncertainNews() {
    try { renderUncertainNews((await invokeAdmin('list_uncertain_news')).items || []); }
    catch (error) { document.getElementById('uncertain-news-list').innerHTML = '<p class="p-4 text-center text-sm text-red-300">목록을 불러오지 못했습니다.</p>'; }
  }

  async function loadAll() {
    const button = document.getElementById('refresh-button');
    button.disabled = true;
    button.classList.add('opacity-60');
    try {
      const status = await invokeAdmin('status');
      applyStatus(status);
      await loadUncertainNews();
      await loadSectorEtfs();
      await loadExtremeNewsRules();
    } catch (error) {
      showNotice('불러오기 실패', error.message || '상태를 확인하지 못했습니다.', true);
    } finally {
      button.disabled = false;
      button.classList.remove('opacity-60');
    }
  }

  async function waitForCompletion(kind, requestedAt) {
    const requested = new Date(requestedAt).getTime() - 5000;
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5000));
      const { run } = await invokeAdmin('workflow_status', { kind });
      setBadge(document.getElementById(kind === 'check' ? 'check-badge' : kind === 'backup' ? 'backup-badge' : 'news-badge'), run);
      if (run && new Date(run.created_at).getTime() >= requested && run.status === 'completed') {
        return run;
      }
    }
    throw new Error('실행 완료 확인 시간이 초과되었습니다. 새로고침해서 상태를 확인해 주세요.');
  }

  async function runWorkflow(kind) {
    const button = document.getElementById(kind === 'check' ? 'run-check-button' : kind === 'backup' ? 'run-backup-button' : 'run-news-button');
    const label = button.querySelector('span');
    const idleLabel = kind === 'check' ? '지금 지표 확인' : kind === 'backup' ? '지금 수동 백업' : '뉴스 분석 테스트';
    button.disabled = true;
    label.textContent = '진행 중';
      setBadge(document.getElementById(kind === 'check' ? 'check-badge' : kind === 'backup' ? 'backup-badge' : 'news-badge'), {
      status: 'queued'
    });
    try {
      const result = await invokeAdmin(kind === 'check' ? 'run_check' : kind === 'backup' ? 'run_backup' : 'run_news');
      const run = await waitForCompletion(kind, result.requested_at);
      if (run.conclusion === 'success') {
        showNotice('실행 완료', kind === 'check' ? '지표 확인이 완료되었습니다.' : kind === 'backup' ? '수동 백업이 완료되었습니다.' : '뉴스 분석 테스트가 완료되었습니다. 결과는 저장하지 않았습니다.');
      } else {
        showNotice('실행 실패', `작업이 ${run.conclusion || '실패'} 상태로 종료되었습니다.`, true);
      }
    } catch (error) {
      showNotice('실행 실패', error.message || '작업을 실행하지 못했습니다.', true);
    } finally {
      button.disabled = false;
      label.textContent = idleLabel;
      await loadAll();
    }
  }

  async function saveSchedule() {
    const button = document.getElementById('save-schedule-button');
    const times = Array.from(document.querySelectorAll('[data-schedule-time]'), (input) => input.value);
    button.disabled = true;
    button.textContent = '저장 중';
    try {
      const result = await invokeAdmin('update_schedule', { times });
      scheduleTimes = result.schedule.times;
      document.getElementById('schedule-label').textContent = `매일 ${scheduleTimes.join(' · ')}`;
      document.getElementById('next-check-time').textContent = nextScheduledCheck(scheduleTimes);
      showNotice('일정 저장 완료', `지표 확인 시간을 ${scheduleTimes.join(', ')}로 변경했습니다.`);
    } catch (error) {
      showNotice('일정 저장 실패', error.message || '일정을 변경하지 못했습니다.', true);
    } finally {
      button.disabled = false;
      button.textContent = '시간 저장';
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
      await loadAll();
      window.dispatchEvent(new CustomEvent('macrowatch:admin-ready'));
    } catch (error) {
      accessMessage.textContent = error.message || '관리자 권한을 확인하지 못했습니다.';
      backLink.classList.remove('hidden');
      backLink.classList.add('flex');
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-button').addEventListener('click', loadAll);
    document.getElementById('run-check-button').addEventListener('click', () => runWorkflow('check'));
    document.getElementById('run-backup-button').addEventListener('click', () => runWorkflow('backup'));
    document.getElementById('run-news-button').addEventListener('click', () => runWorkflow('news'));
    document.getElementById('save-schedule-button').addEventListener('click', saveSchedule);
    document.getElementById('schedule-count').addEventListener('change', () => {
      const current = Array.from(document.querySelectorAll('[data-schedule-time]'), (input) => input.value);
      renderScheduleTimeInputs(current);
    });
    document.getElementById('refresh-uncertain-button').addEventListener('click', loadUncertainNews);
    document.getElementById('sector-etf-form').addEventListener('submit', addSectorEtf);
    document.getElementById('extreme-news-rule-form').addEventListener('submit', addExtremeNewsRule);
    document.getElementById('operation-close').addEventListener('click', hideNotice);
    document.getElementById('operation-modal').addEventListener('click', (event) => {
      if (event.target === event.currentTarget) hideNotice();
    });
    authorizeAdmin();
  });
})();

