from pathlib import Path

html_path = Path('economic-charts.html')
html = html_path.read_text(encoding='utf-8')
old = '''      <nav class="economic-nav" aria-label="MacroWatch 메뉴">
        <a href="index.html#news">플로우 분석</a><a href="index.html#policy-signals">통화정책</a><a href="index.html#earnings-signals">기업이익</a><a href="index.html#stress">스트레스</a><a href="index.html#tracker">지표추적</a><a class="is-active" href="economic-charts.html" aria-current="page">경제지표 챠트</a>
      </nav>
'''
new = '''      <nav class="economic-nav" aria-label="MacroWatch 메뉴">
        <a href="index.html#news">플로우 분석</a><a href="index.html#policy-signals">통화정책</a><a href="index.html#earnings-signals">기업이익</a><a href="index.html#stress">스트레스</a><a href="index.html#tracker">지표추적</a><a class="is-active" href="economic-charts.html" aria-current="page">경제지표 챠트</a>
      </nav>
      <div class="economic-account-actions" aria-label="계정 메뉴">
        <button id="economic-profile-button" type="button" aria-label="개인 설정" title="개인 설정"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8a7 7 0 0 1 14 0H5Z"/></svg></button>
        <a id="economic-admin-link" href="admin.html" aria-label="관리자 페이지" title="관리자 페이지" hidden><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm9 3.5-2.1-.8a7.8 7.8 0 0 0-.6-1.5l.9-2.1-2.8-2.8-2.1.9a7.8 7.8 0 0 0-1.5-.6L12 3H8l-.8 2.1a7.8 7.8 0 0 0-1.5.6l-2.1-.9L.8 7.6l.9 2.1a7.8 7.8 0 0 0-.6 1.5L-1 12v4l2.1.8c.2.5.4 1 .6 1.5l-.9 2.1 2.8 2.8 2.1-.9c.5.3 1 .5 1.5.6L8 25h4l.8-2.1c.5-.2 1-.4 1.5-.6l2.1.9 2.8-2.8-.9-2.1c.3-.5.5-1 .6-1.5L21 16v-4Z" transform="scale(.8) translate(2.5 2.5)"/></svg></a>
        <button id="economic-logout-button" type="button" aria-label="로그아웃" title="로그아웃"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4H4v16h6v-2H6V6h4V4Zm5.6 3.6-1.4 1.4 2 2H9v2h7.2l-2 2 1.4 1.4L20 12l-4.4-4.4Z"/></svg></button>
      </div>
'''
assert old in html, 'economic nav block not found'
html = html.replace(old, new, 1)
old = '  <script src="assets/js/charts/economic-series-manager.js?v=3"></script>\n'
new = '  <script src="assets/js/charts/economic-series-manager.js?v=3"></script>\n  <script src="assets/js/charts/economic-page-actions.js?v=1"></script>\n'
assert old in html, 'economic scripts marker not found'
html = html.replace(old, new, 1)
html_path.write_text(html, encoding='utf-8')

css_path = Path('assets/css/economic-charts.css')
css = css_path.read_text(encoding='utf-8')
marker = '.economic-nav a.is-active{background:#111827;color:#fff}'
addition = '.economic-account-actions{margin-left:auto;display:flex;align-items:center;gap:6px}.economic-account-actions button,.economic-account-actions a{box-sizing:border-box;display:grid;place-items:center;width:34px;height:34px;border:1px solid #d1d5db;border-radius:9px;background:#fff;color:#4b5563;text-decoration:none;cursor:pointer}.economic-account-actions button:hover,.economic-account-actions a:hover{background:#f3f4f6;color:#111827}.economic-account-actions svg{width:16px;height:16px;fill:currentColor}.economic-account-actions [hidden]{display:none!important}'
assert marker in css, 'economic nav css marker not found'
css = css.replace(marker, marker + addition, 1)
css = css.replace('@media(max-width:850px){.economic-topbar{align-items:flex-start;flex-direction:column}', '@media(max-width:850px){.economic-topbar{align-items:flex-start;flex-direction:column}.economic-account-actions{position:absolute;right:10px;top:10px}', 1)
css_path.write_text(css, encoding='utf-8')

actions_path = Path('assets/js/charts/economic-page-actions.js')
actions_path.write_text("""(() => {\n  'use strict';\n  const client = window.MacroWatchFrontend?.createSupabaseClient?.();\n  const profileButton = document.getElementById('economic-profile-button');\n  const adminLink = document.getElementById('economic-admin-link');\n  const logoutButton = document.getElementById('economic-logout-button');\n\n  profileButton?.addEventListener('click', () => {\n    window.sessionStorage.setItem('macrowatch.open-profile', '1');\n    window.location.href = 'index.html';\n  });\n\n  logoutButton?.addEventListener('click', async () => {\n    logoutButton.disabled = true;\n    try { await client?.auth.signOut(); }\n    finally { window.location.replace('index.html'); }\n  });\n\n  async function updateAdminAccess() {\n    if (!client || !adminLink) return;\n    adminLink.hidden = true;\n    const { data: sessionData } = await client.auth.getSession();\n    const userId = sessionData.session?.user?.id;\n    if (!userId) return;\n    const { data, error } = await client.from('user_accounts').select('is_admin').eq('user_id', userId).maybeSingle();\n    if (!error && data?.is_admin === true) adminLink.hidden = false;\n  }\n\n  updateAdminAccess().catch((error) => console.warn('economic chart admin access check failed', error));\n})();\n""", encoding='utf-8')

auth_path = Path('assets/js/core/auth.js')
auth = auth_path.read_text(encoding='utf-8')
old = '''    await updateAdminLink();
    await window.MacroWatchDashboard?.loadAll();
  }
'''
new = '''    await updateAdminLink();
    await window.MacroWatchDashboard?.loadAll();
    if (window.sessionStorage.getItem('macrowatch.open-profile') === '1') {
      window.sessionStorage.removeItem('macrowatch.open-profile');
      window.requestAnimationFrame(() => document.getElementById('profile-button')?.click());
    }
  }
'''
assert old in auth, 'showDashboard marker not found'
auth = auth.replace(old, new, 1)
auth_path.write_text(auth, encoding='utf-8')

test_path = Path('tests/test_dashboard.js')
tests = test_path.read_text(encoding='utf-8')
marker = "test('HTML inline 이벤트가 사용하는 핸들러만 명시적으로 공개한다', () => {"
assert marker in tests, 'dashboard test marker not found'
addition = r'''test('경제지표 차트는 계정 액션과 관리자 권한 노출을 제공한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'economic-charts.html'), 'utf8');
  const actions = fs.readFileSync(path.join(__dirname, '..', 'assets/js/charts/economic-page-actions.js'), 'utf8');
  const auth = fs.readFileSync(path.join(__dirname, '..', 'assets/js/core/auth.js'), 'utf8');
  assert.match(html, /id="economic-profile-button"/);
  assert.match(html, /id="economic-admin-link"[^>]*hidden/);
  assert.match(html, /id="economic-logout-button"/);
  assert.match(actions, /select\('is_admin'\)/);
  assert.match(actions, /data\?\.is_admin === true/);
  assert.match(actions, /auth\.signOut\(\)/);
  assert.match(actions, /macrowatch\.open-profile/);
  assert.match(auth, /macrowatch\.open-profile/);
  assert.match(auth, /getElementById\('profile-button'\)\?\.click\(\)/);
});

'''
tests = tests.replace(marker, addition + marker, 1)
test_path.write_text(tests, encoding='utf-8')
