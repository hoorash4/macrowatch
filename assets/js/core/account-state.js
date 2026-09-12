(() => {
  'use strict';

  function create(client) {
    if (!client) throw new Error('Supabase client is required for account state.');
    const functionClient = window.MacroWatchFrontend.createFunctionClient(client);

    async function invoke(name, action, payload = {}) {
      return functionClient.invoke(name, { action, ...payload }, {
        errorMessage: status => `요청에 실패했습니다. (${status})`,
      });
    }

    function setKakaoStatus(connected, message) {
      const status = document.getElementById('kakao-connection-status');
      const badge = document.getElementById('kakao-status-badge');
      const unlink = document.getElementById('kakao-unlink-button');
      if (status) status.textContent = message || (connected ? '로그인 한 카카오 계정으로 지표 변동 알림이 전송 됩니다.' : '카카오 알림 연결을 확인하지 못했습니다.');
      if (badge) {
        badge.textContent = connected ? '연결됨' : '확인 필요';
        badge.className = connected
          ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
          : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
      }
      unlink?.classList.toggle('hidden', !connected);
    }

    function setEmailStatus(address, active, message) {
      const status = document.getElementById('email-alert-status');
      const badge = document.getElementById('email-alert-badge');
      const input = document.getElementById('email-alert-address');
      const remove = document.getElementById('email-alert-remove-button');
      if (status) status.textContent = message || (active ? `${address}로 지표 변동 알림을 받습니다.` : '이메일 알림을 설정하지 않았습니다.');
      if (badge) {
        badge.textContent = active ? '사용 중' : '미설정';
        badge.className = active
          ? 'shrink-0 rounded-full border border-emerald-700/50 bg-emerald-950/60 px-2.5 py-1 text-[11px] font-semibold text-emerald-400'
          : 'shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400';
      }
      if (input) input.value = address || '';
      remove?.classList.toggle('hidden', !active);
    }

    async function loadAccountRow(userId) {
      const output = document.getElementById('profile-username');
      if (output) output.textContent = '확인 중';
      const { data, error } = await client
        .from('user_accounts')
        .select('username,theme_preference')
        .eq('user_id', userId)
        .maybeSingle();
      if (error) throw error;
      if (output) output.textContent = data?.username || '아이디 미등록 · 카카오 전용 계정';
      const preference = data?.theme_preference || 'system';
      window.MacroWatchTheme?.applyPreference?.(preference);
      const select = document.getElementById('theme-preference');
      if (select) select.value = preference;
      return data;
    }

    async function loadKakao() {
      setKakaoStatus(false, '연결 상태 확인 중');
      const data = await invoke('kakao-auth', 'status');
      setKakaoStatus(Boolean(data?.connected));
      return data;
    }

    async function loadEmail() {
      setEmailStatus('', false, '설정 상태 확인 중');
      const data = await invoke('notification-settings', 'status');
      setEmailStatus(data?.address || '', Boolean(data?.is_active));
      return data;
    }

    async function refresh() {
      const { data: sessionData, error } = await client.auth.getSession();
      if (error) throw error;
      const userId = sessionData?.session?.user?.id;
      if (!userId) throw new Error('로그인이 필요합니다.');

      const results = await Promise.allSettled([
        loadAccountRow(userId),
        loadKakao(),
        loadEmail(),
      ]);

      if (results[0].status === 'rejected') {
        const output = document.getElementById('profile-username');
        if (output) output.textContent = '아이디를 확인하지 못했습니다.';
        console.error('account profile load failed', results[0].reason);
      }
      if (results[1].status === 'rejected') {
        setKakaoStatus(false, results[1].reason?.message || '연결 상태를 확인하지 못했습니다.');
        console.error('kakao status load failed', results[1].reason);
      }
      if (results[2].status === 'rejected') {
        setEmailStatus('', false, results[2].reason?.message || '이메일 설정을 확인하지 못했습니다.');
        console.error('email settings load failed', results[2].reason);
      }
      return results;
    }

    function bindTheme() {
      const select = document.getElementById('theme-preference');
      if (!select || select.dataset.accountStateBound === 'true') return;
      select.dataset.accountStateBound = 'true';
      select.addEventListener('change', async () => {
        select.disabled = true;
        try {
          await window.MacroWatchTheme?.savePreference?.(select.value);
        } catch (error) {
          window.alert(error?.message || '테마 설정을 저장하지 못했습니다.');
        } finally {
          select.disabled = false;
        }
      });
    }

    return Object.freeze({ refresh, bindTheme, loadKakao, loadEmail });
  }

  window.MacroWatchAccountState = Object.freeze({ create });
})();
