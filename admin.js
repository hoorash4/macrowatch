(() => {
  const SUPABASE_URL = 'https://xhghpywvthjuvespzdul.supabase.co';
  const SUPABASE_KEY = 'sb_publishable_rPKY5Wfpp1JnSkPhIzJqJA_cijBqYgc';
  const db = window.supabase ? window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY) : null;
  let scheduleTimes = ['08:00', '18:00'];

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

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"]/g, (character) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;'
    })[character]);
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

  async function getAccessToken() {
    const { data, error } = await db.auth.getSession();
    if (error || !data.session?.access_token) {
      throw new Error('로그인이 필요합니다.');
    }
    return data.session.access_token;
  }

  async function invokeAdmin(action, payload = {}, retried = false) {
    const response = await fetch(`${SUPABASE_URL}/functions/v1/admin-control`, {
      method: 'POST',
      headers: {
        apikey: SUPABASE_KEY,
        Authorization: `Bearer ${await getAccessToken()}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ action, ...payload })
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && !retried) {
      const refreshed = await db.auth.refreshSession();
      if (!refreshed.error && refreshed.data.session) {
        return invokeAdmin(action, payload, true);
      }
    }
    if (!response.ok) throw new Error(data?.error || `관리자 요청에 실패했습니다. (${response.status})`);
    if (data?.error) throw new Error(data.error);
    return data;
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

    scheduleTimes = Array.isArray(data.schedule?.times) ? data.schedule.times : ['08:00', '18:00'];
    document.getElementById('schedule-time-1').value = scheduleTimes[0] || '08:00';
    document.getElementById('schedule-time-2').value = scheduleTimes[1] || '18:00';
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

  async function loadAll() {
    const button = document.getElementById('refresh-button');
    button.disabled = true;
    button.classList.add('opacity-60');
    try {
      const status = await invokeAdmin('status');
      applyStatus(status);
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
      setBadge(document.getElementById(kind === 'check' ? 'check-badge' : 'backup-badge'), run);
      if (run && new Date(run.created_at).getTime() >= requested && run.status === 'completed') {
        return run;
      }
    }
    throw new Error('실행 완료 확인 시간이 초과되었습니다. 새로고침해서 상태를 확인해 주세요.');
  }

  async function runWorkflow(kind) {
    const button = document.getElementById(kind === 'check' ? 'run-check-button' : 'run-backup-button');
    const label = button.querySelector('span');
    const idleLabel = kind === 'check' ? '지금 지표 확인' : '지금 수동 백업';
    button.disabled = true;
    label.textContent = '진행 중';
    setBadge(document.getElementById(kind === 'check' ? 'check-badge' : 'backup-badge'), {
      status: 'queued'
    });
    try {
      const result = await invokeAdmin(kind === 'check' ? 'run_check' : 'run_backup');
      const run = await waitForCompletion(kind, result.requested_at);
      if (run.conclusion === 'success') {
        showNotice('실행 완료', kind === 'check' ? '지표 확인이 완료되었습니다.' : '수동 백업이 완료되었습니다.');
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
    const times = [
      document.getElementById('schedule-time-1').value,
      document.getElementById('schedule-time-2').value
    ];
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
    document.getElementById('save-schedule-button').addEventListener('click', saveSchedule);
    document.getElementById('operation-close').addEventListener('click', hideNotice);
    document.getElementById('operation-modal').addEventListener('click', (event) => {
      if (event.target === event.currentTarget) hideNotice();
    });
    authorizeAdmin();
  });
})();
