(() => {
  'use strict';

  if (window.MacroWatchAccountModal) return;
  let mounted = false;

  function syncDashboardLogo() {
    const logo = document.querySelector('.dashboard-logo');
    if (!logo) return;
    const fileName = document.documentElement.dataset.theme === 'dark'
      ? 'mw_logo_w.png'
      : 'mw_logo.png';
    logo.style.backgroundImage = `url("images/${fileName}")`;
  }

  function normalize() {
    const modal = document.getElementById('profile-modal');
    const deleteModal = document.getElementById('account-delete-modal');
    const themeCard = document.getElementById('theme-preference')?.closest('.theme-preference-card');
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
    if (themeCard) {
      themeCard.style.background = 'rgba(2, 6, 23, .6)';
      themeCard.style.borderColor = '#1e293b';
      const label = themeCard.querySelector('label');
      const help = themeCard.querySelector('p');
      const select = themeCard.querySelector('select');
      if (label) label.style.color = '#cbd5e1';
      if (help) help.style.color = '#64748b';
      if (select) {
        select.style.background = '#0f172a';
        select.style.borderColor = '#334155';
        select.style.color = '#e2e8f0';
      }
    }
  }

  function ensure() {
    if (mounted || !document.body) return;
    for (const id of ['profile-modal', 'account-delete-modal', 'service-preparing-modal']) document.getElementById(id)?.remove();
    document.body.insertAdjacentHTML('beforeend', `
      <div id="service-preparing-modal" class="modal-overlay modal-overlay--notice hidden" role="dialog" aria-modal="true" aria-labelledby="service-preparing-title">
        <section class="w-full max-w-xs rounded-2xl border border-slate-700 bg-slate-900 p-6 text-center shadow-2xl shadow-black/40">
          <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-full border border-blue-500/30 bg-blue-500/10 text-xl text-blue-400"><i class="fa-solid fa-hourglass-half"></i></div>
          <h2 id="service-preparing-title" class="mt-4 text-lg font-bold text-white">서비스 준비 중입니다.</h2>
          <p id="service-preparing-description" class="mt-2 text-xs leading-relaxed text-slate-400">ID/PW 로그인과 회원가입은 추후 제공할 예정입니다.</p>
          <button id="service-preparing-close" type="button" class="mt-5 w-full rounded-lg bg-blue-600 py-2.5 text-sm font-bold text-white transition hover:bg-blue-500">확인</button>
        </section>
      </div>
      <div id="profile-modal" class="modal-overlay hidden" role="dialog" aria-modal="true" aria-labelledby="profile-modal-title">
        <section class="profile-dialog w-full max-w-xl rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
          <div class="profile-dialog-header flex items-center justify-between">
            <div><h2 id="profile-modal-title" class="text-lg font-bold text-white"><i class="fa-solid fa-user-gear mr-2 text-blue-400"></i>개인 설정</h2><p class="mt-1 text-xs text-slate-500">비밀번호와 알림 계정 연결 상태를 관리합니다.</p></div>
            <button id="profile-close-button" type="button" aria-label="개인 설정 닫기" class="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-800 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
          </div>
          <div class="profile-dialog-body">
            <div class="mt-5 flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/60 px-4 py-3"><div><p class="text-xs text-slate-500">현재 아이디</p><p id="profile-username" class="mt-1 text-sm font-bold text-slate-200">확인 중</p></div><i class="fa-solid fa-id-card text-slate-600"></i></div>
            <form id="password-change-form" autocomplete="off" class="mt-4 rounded-xl border border-slate-800 bg-slate-950/60 p-4"><p class="text-sm font-bold text-slate-200"><i class="fa-solid fa-key mr-2 text-blue-400"></i>비밀번호 변경</p><div class="mt-3 grid gap-2 sm:grid-cols-3"><input id="current-password" type="password" autocomplete="off" required placeholder="현재 비밀번호" class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-blue-500"><input id="new-password" type="password" autocomplete="off" required placeholder="새 비밀번호" class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-blue-500"><input id="confirm-password" type="password" autocomplete="off" required placeholder="새 비밀번호 확인" class="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-blue-500"></div><button type="submit" class="mt-3 w-full rounded-lg border border-blue-700/60 bg-blue-950/40 py-2.5 text-sm font-bold text-blue-300 hover:bg-blue-900/50">비밀번호 변경</button></form>
            <div class="mt-4 rounded-xl border border-slate-800 bg-slate-950/60 p-4"><div class="flex items-center justify-between gap-3"><div><p class="text-sm font-bold text-slate-200"><i class="fa-solid fa-comment mr-2 text-yellow-400"></i>카카오톡 알림</p><p id="kakao-connection-status" class="mt-1 text-xs text-slate-500">연결 상태 확인 중</p></div><span id="kakao-status-badge" class="shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400">확인 중</span></div><button id="kakao-connect-button" type="button" class="mt-4 flex w-full items-center justify-center rounded-lg bg-yellow-500 py-2.5 text-sm font-bold text-slate-950 transition hover:bg-yellow-400 disabled:cursor-wait disabled:opacity-60"><i class="fa-solid fa-link mr-2"></i><span>카카오 계정 다시 연결하기</span></button><button id="kakao-unlink-button" type="button" class="mt-2 hidden w-full rounded-lg border border-amber-800/70 py-2.5 text-sm font-bold text-amber-300 hover:bg-amber-950/40">카카오 연동 해제</button></div>
            <div class="mt-4 rounded-xl border border-slate-800 bg-slate-950/60 p-4"><div class="flex items-center justify-between gap-3"><div><p class="text-sm font-bold text-slate-200"><i class="fa-solid fa-envelope mr-2 text-sky-400"></i>이메일 알림</p><p id="email-alert-status" class="mt-1 text-xs text-slate-500">설정 상태 확인 중</p></div><span id="email-alert-badge" class="shrink-0 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-1 text-[11px] font-semibold text-slate-400">확인 중</span></div><label for="email-alert-address" class="sr-only">이메일 수신 주소</label><div class="mt-4 flex gap-2"><input id="email-alert-address" type="email" autocomplete="email" placeholder="name@example.com" class="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2.5 text-sm text-white placeholder:text-slate-500 focus:border-sky-500 focus:outline-none"><button id="email-alert-save-button" type="button" class="shrink-0 rounded-lg bg-sky-500 px-4 py-2.5 text-sm font-bold text-slate-950 hover:bg-sky-400 disabled:cursor-wait disabled:opacity-60">저장</button></div><button id="email-alert-remove-button" type="button" class="mt-2 hidden w-full rounded-lg border border-slate-700 py-2.5 text-sm font-bold text-slate-300 hover:bg-slate-800">이메일 알림 해제</button></div>
            <section class="theme-preference-card"><label for="theme-preference">화면 테마</label><select id="theme-preference" aria-label="화면 테마"><option value="system">시스템 설정</option><option value="light">라이트 모드</option><option value="dark">다크 모드</option></select><p>시스템 설정은 기기의 라이트/다크 모드 변경을 실시간으로 따릅니다.</p></section>
            <div class="mt-4 rounded-xl border border-slate-800 bg-slate-950/60 p-4"><button id="account-delete-button" type="button" class="mt-4 flex w-full items-center justify-center rounded-lg border border-red-800/70 bg-red-950/30 py-2.5 text-sm font-bold text-red-400 transition hover:bg-red-950/60"><i class="fa-solid fa-user-slash mr-2"></i>회원 탈퇴</button></div>
          </div>
        </section>
      </div>
      <div id="account-delete-modal" class="modal-overlay modal-overlay--critical hidden"><section class="w-full max-w-sm rounded-2xl border border-red-900/60 bg-slate-900 p-6 text-center shadow-2xl"><i class="fa-solid fa-triangle-exclamation text-3xl text-red-400"></i><h2 class="mt-4 text-lg font-bold text-white">회원 탈퇴를 진행할까요?</h2><p class="mt-2 text-sm leading-relaxed text-slate-400">등록한 지표와 알림 설정이 모두 삭제되며 되돌릴 수 없습니다.</p><div class="mt-5 flex gap-2"><button id="account-delete-cancel" type="button" class="flex-1 rounded-lg bg-slate-800 py-2.5 text-sm font-bold text-slate-300 hover:bg-slate-700">취소</button><button id="account-delete-confirm" type="button" class="flex-1 rounded-lg bg-red-600 py-2.5 text-sm font-bold text-white hover:bg-red-500 disabled:opacity-60">탈퇴하기</button></div></section></div>`);
    mounted = true;
    normalize();
    syncDashboardLogo();
  }

  window.MacroWatchAccountModal = { ensure };
  window.MacroWatchAccountModal.normalize = normalize;
  window.addEventListener('macrowatch:themechange', syncDashboardLogo);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ensure, { once: true });
  else ensure();
})();
