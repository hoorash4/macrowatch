from pathlib import Path

# 1) 경제지표 차트가 실제 사용하는 Supabase 클라이언트를 계정 액션과 공유한다.
p = Path('assets/js/charts/economic-charts.js')
s = p.read_text(encoding='utf-8')
old = "const supabaseClient=window.supabase?.createClient(cfg.supabaseUrl,cfg.supabasePublishableKey);"
new = old + "\nwindow.macroWatchEconomicSupabase = supabaseClient;"
assert old in s and 'window.macroWatchEconomicSupabase = supabaseClient;' not in s
p.write_text(s.replace(old, new, 1), encoding='utf-8')

# 2) 경제지표 페이지에 기존 개인설정 화면을 렌더링할 수 있는 공통 스타일 기반과 계정 버튼을 연결한다.
p = Path('economic-charts.html')
s = p.read_text(encoding='utf-8')
assert 'id="economic-profile-button"' not in s
s = s.replace(
    '  <link rel="stylesheet" crossorigin href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">',
    '  <script src="https://cdn.tailwindcss.com"></script>\n  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">\n  <link rel="stylesheet" crossorigin href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">\n  <link rel="stylesheet" href="assets/css/styles.css?v=188">',
    1,
)
nav_end = '      </nav>\n    </header>'
actions = '''      </nav>\n      <div class="economic-account-actions" aria-label="계정 메뉴">\n        <button id="economic-profile-button" type="button" aria-label="개인 설정" title="개인 설정"><i class="fa-solid fa-user"></i></button>\n        <a id="economic-admin-link" href="admin.html" aria-label="관리자 페이지" title="관리자 페이지" hidden><i class="fa-solid fa-gear"></i></a>\n        <button id="economic-logout-button" type="button" aria-label="로그아웃" title="로그아웃"><i class="fa-solid fa-right-from-bracket"></i></button>\n      </div>\n    </header>'''
assert nav_end in s
s = s.replace(nav_end, actions, 1)
script_marker = '  <script src="assets/js/charts/economic-series-manager.js?v=3"></script>'
assert script_marker in s
s = s.replace(script_marker, script_marker + '\n  <script src="assets/js/core/economic-account-actions.js?v=1"></script>', 1)
p.write_text(s, encoding='utf-8')

# 3) 상단 계정 액션만 경제지표 레이아웃에 맞게 스타일링한다. 개인설정 모달 자체 스타일은 기존 styles.css를 그대로 쓴다.
p = Path('assets/css/economic-charts.css')
s = p.read_text(encoding='utf-8')
assert '.economic-account-actions{' not in s
s += '''\n.economic-account-actions{margin-left:auto;display:flex;align-items:center;gap:6px}.economic-account-actions button,.economic-account-actions a{box-sizing:border-box;display:grid;place-items:center;width:34px;height:34px;border:1px solid #d1d5db;border-radius:9px;background:#fff;color:#4b5563;text-decoration:none;cursor:pointer}.economic-account-actions button:hover,.economic-account-actions a:hover{background:#f3f4f6;color:#111827}.economic-account-actions [hidden]{display:none!important}@media(max-width:850px){.economic-account-actions{position:absolute;right:10px;top:10px}}\n'''
p.write_text(s, encoding='utf-8')

# 4) 회귀 계약: 홈 이동으로 개인설정을 여는 구현을 금지하고, 기존 모달과 실제 경제지표 세션 공유를 강제한다.
p = Path('tests/test_dashboard.js')
s = p.read_text(encoding='utf-8')
marker = "test('공통 커서 글자는 보이는 플롯 폭 안으로 이동한다', () => {"
assert marker in s
addition = r'''

test('경제지표 계정 액션은 기존 개인설정 화면과 실제 경제지표 세션을 공유한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'economic-charts.html'), 'utf8');
  const chart = fs.readFileSync(path.join(__dirname, '..', 'assets/js/charts/economic-charts.js'), 'utf8');
  const actions = fs.readFileSync(path.join(__dirname, '..', 'assets/js/core/economic-account-actions.js'), 'utf8');
  assert.match(html, /id="economic-profile-button"/);
  assert.match(html, /id="economic-admin-link"/);
  assert.match(html, /id="economic-logout-button"/);
  assert.match(chart, /window\.macroWatchEconomicSupabase\s*=\s*supabaseClient/);
  assert.match(actions, /const client = window\.macroWatchEconomicSupabase/);
  assert.match(actions, /fetch\('index\.html'/);
  assert.match(actions, /getElementById\('profile-modal'\)/);
  assert.match(actions, /select\('is_admin'\)/);
  assert.match(actions, /auth\.signOut\(\{ scope: 'local' \}\)/);
  assert.doesNotMatch(actions, /sessionStorage\.setItem\('macrowatch\.open-profile'/);
  assert.doesNotMatch(actions, /profileButton[\s\S]{0,300}location\.(?:href|replace)\s*=.*index\.html/);
});
'''
s = s.replace(marker, addition + '\n' + marker, 1)
p.write_text(s, encoding='utf-8')
