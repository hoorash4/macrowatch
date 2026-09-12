from pathlib import Path

ROOT = Path('.')

def read(path):
    return (ROOT / path).read_bytes().decode('utf-8')

def write(path, text):
    (ROOT / path).write_bytes(text.encode('utf-8'))

def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f'{label}: marker not found')
    return text.replace(old, new, 1)

# 1) Move the existing economic chart body into the main dashboard as one normal menu panel.
index = read('index.html')
economic_page = read('economic-charts.html')

main_start = economic_page.index('  <main class="economic-main">')
main_end = economic_page.index('  </main>', main_start) + len('  </main>')
economic_main = economic_page[main_start:main_end]
modal_start = economic_page.index('  <div id="economic-alert-modal"')
modal_end = economic_page.index('  <script ', modal_start)
economic_modal = economic_page[modal_start:modal_end].rstrip('\r\n')

economic_panel = (
    '    <section id="economic-dashboard-panel" class="economic-dashboard-panel dashboard-panel-hidden" data-dashboard-panel="economic">\r\n'
    + economic_main.replace('\n', '\r\n') + '\r\n\r\n'
    + economic_modal.replace('\n', '\r\n') + '\r\n'
    '    </section>\r\n\r\n'
)

index = replace_once(
    index,
    '  <link rel="stylesheet" href="assets/css/styles.css?v=188">\n</head>',
    '  <link rel="stylesheet" href="assets/css/styles.css?v=188">\n'
    '  <link rel="stylesheet" href="assets/css/economic-charts.css?v=8">\n'
    '  <link rel="stylesheet" href="assets/css/economic-series-manager.css?v=1">\n'
    '  <script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>\n'
    '</head>',
    'economic assets in head',
)

index = replace_once(
    index,
    '          <button type="button" class="dashboard-nav-item" data-dashboard-view="tracker"><i class="fa-solid fa-bell"></i><span>지표 추적 알림</span></button>\r\n          <div class="dashboard-nav-actions">',
    '          <button type="button" class="dashboard-nav-item" data-dashboard-view="tracker"><i class="fa-solid fa-bell"></i><span>지표 추적 알림</span></button>\r\n'
    '          <button type="button" class="dashboard-nav-item" data-dashboard-view="economic"><i class="fa-solid fa-chart-line"></i><span>경제지표 챠트</span></button>\r\n'
    '          <div class="dashboard-nav-actions">',
    'desktop economic menu',
)

mobile_marker = (
    '    <nav class="mobile-bottom-nav" aria-label="모바일 대시보드 메뉴">\r\n'
    '      <button type="button" data-mobile-dashboard-view="overview" aria-current="page">'
)
mobile_pos = index.index(mobile_marker)
tracker_mobile = (
    '      <button type="button" data-mobile-dashboard-view="tracker">\r\n'
    '        <i class="fa-solid fa-bell" aria-hidden="true"></i><span>지표추적</span>\r\n'
    '      </button>\r\n'
)
tracker_pos = index.index(tracker_mobile, mobile_pos)
insert_pos = tracker_pos + len(tracker_mobile)
index = index[:insert_pos] + (
    '      <button type="button" data-mobile-dashboard-view="economic">\r\n'
    '        <i class="fa-solid fa-chart-line" aria-hidden="true"></i><span>경제지표</span>\r\n'
    '      </button>\r\n'
) + index[insert_pos:]

index = replace_once(
    index,
    '    </div>\r\n    <!-- 05. 별도 메뉴의 지표 추적 카드 -->',
    economic_panel + '    </div>\r\n    <!-- 05. 별도 메뉴의 지표 추적 카드 -->',
    'economic dashboard panel',
)

index = replace_once(
    index,
    '  <script src="assets/js/charts/korea-earnings-chart.js?v=40"></script>\n  <script src="assets/js/policy/policy-briefing.js?v=5"></script>',
    '  <script src="assets/js/charts/korea-earnings-chart.js?v=40"></script>\n'
    '  <script src="assets/js/charts/economic-charts.js?v=13"></script>\n'
    '  <script src="assets/js/charts/economic-series-manager.js?v=4"></script>\n'
    '  <script src="assets/js/policy/policy-briefing.js?v=5"></script>',
    'economic scripts',
)
write('index.html', index)

# 2) Dashboard navigation: economic is a normal dashboard view and only widens the shell while active.
script = read('assets/js/dashboard/script.js')
script = replace_once(
    script,
    "    tracker: '#tracker',\n",
    "    tracker: '#tracker',\n    economic: '#economic-charts',\n",
    'economic hash mapping',
)
script = replace_once(
    script,
    "      if (selectedView === 'stress') selectedStressMarket = 'credit';\n    }\n\n    desktopButtons.forEach",
    "      if (selectedView === 'stress') selectedStressMarket = 'credit';\n    }\n\n    document.body.classList.toggle('dashboard-economic-view', selectedView === 'economic');\n\n    desktopButtons.forEach",
    'economic body view class',
)
write('assets/js/dashboard/script.js', script)

# 3) Economic chart code now reuses the main app session and initializes only when its menu is shown.
econ = read('assets/js/charts/economic-charts.js')
econ = replace_once(
    econ,
    "const supabaseClient=window.supabase?.createClient(cfg.supabaseUrl,cfg.supabasePublishableKey);",
    "const supabaseClient=window.macroWatchSupabase||window.supabase?.createClient(cfg.supabaseUrl,cfg.supabasePublishableKey);",
    'reuse main supabase client',
)
econ = econ.replace("url:'economic-charts.html'", "url:'index.html#economic-charts'")
old_init = "async function initialize(){if(!supabaseClient||!window.LightweightCharts)return;const {data}=await supabaseClient.auth.getSession();if(!data.session){location.replace('index.html');return;}user=data.session.user;await loadPreferences();renderList();await loadAlerts();$('economic-line-tool').onclick=()=>{lineMode=!lineMode;$('economic-line-tool').classList.toggle('is-active',lineMode);};$('economic-delete-line').onclick=deleteLine;$('economic-clear-lines').onclick=clearLines;$('economic-fit-max').onclick=fitMax;document.querySelectorAll('[data-close-alert]').forEach(n=>n.onclick=closeModal);$('economic-alert-form').onsubmit=e=>saveAlert(e).catch(showAlertError);$('economic-alert-delete').onclick=()=>deleteAlert().catch(showAlertError);const initial=firstVisibleSeries();if(initial)await selectSeries(initial);}\ndocument.addEventListener('DOMContentLoaded',initialize);"
new_init = "let initialized=false,initializing=false;\nasync function initialize(){if(initialized||initializing||!$('economic-chart-host')||!supabaseClient||!window.LightweightCharts)return;initializing=true;try{const {data}=await supabaseClient.auth.getSession();if(!data.session)return;user=data.session.user;await loadPreferences();renderList();await loadAlerts();$('economic-line-tool').onclick=()=>{lineMode=!lineMode;$('economic-line-tool').classList.toggle('is-active',lineMode);};$('economic-delete-line').onclick=deleteLine;$('economic-clear-lines').onclick=clearLines;$('economic-fit-max').onclick=fitMax;document.querySelectorAll('[data-close-alert]').forEach(n=>n.onclick=closeModal);$('economic-alert-form').onsubmit=e=>saveAlert(e).catch(showAlertError);$('economic-alert-delete').onclick=()=>deleteAlert().catch(showAlertError);const initial=firstVisibleSeries();if(initial)await selectSeries(initial);initialized=true;}finally{initializing=false;}}\nfunction initializeForEconomicView(event){if(event?.detail?.view==='economic'||location.hash==='#economic-charts')initialize().catch(error=>{console.error(error);const status=$('economic-status');if(status)status.textContent='초기화 오류';});}\nwindow.addEventListener('macrowatch:dashboard-view-changed',initializeForEconomicView);\ndocument.addEventListener('DOMContentLoaded',()=>{if(location.hash==='#economic-charts')initializeForEconomicView();});"
econ = replace_once(econ, old_init, new_init, 'economic lazy initialization')
write('assets/js/charts/economic-charts.js', econ)

# 4) Series manager follows the same main-app session and activation lifecycle.
manager = read('assets/js/charts/economic-series-manager.js')
manager = replace_once(
    manager,
    "const client = window.supabase?.createClient(cfg.supabaseUrl, cfg.supabasePublishableKey);",
    "const client = window.macroWatchSupabase || window.supabase?.createClient(cfg.supabaseUrl, cfg.supabasePublishableKey);",
    'series manager main client',
)
manager = replace_once(
    manager,
    "let user = null;\nlet modal = null;\nlet observer = null;",
    "let user = null;\nlet modal = null;\nlet observer = null;\nlet initialized = false;\nlet initializing = false;",
    'series manager init state',
)
old_manager_init = "async function initialize() {\n  if (!client) return;\n  buildModal();\n  const {data} = await client.auth.getSession();\n  user = data.session?.user || null;\n  if (!user) return;\n  ensureAddButton();\n  const root = $('economic-series-list');\n  if (root) {\n    observer = new MutationObserver(ensureAddButton);\n    observer.observe(root, {childList:true});\n  }\n}\n\ndocument.addEventListener('DOMContentLoaded', initialize);"
new_manager_init = "async function initialize() {\n  if (initialized || initializing || !client || !$('economic-series-list')) return;\n  initializing = true;\n  try {\n    buildModal();\n    const {data} = await client.auth.getSession();\n    user = data.session?.user || null;\n    if (!user) return;\n    ensureAddButton();\n    const root = $('economic-series-list');\n    if (root && !observer) {\n      observer = new MutationObserver(ensureAddButton);\n      observer.observe(root, {childList:true});\n    }\n    initialized = true;\n  } finally {\n    initializing = false;\n  }\n}\n\nfunction initializeForEconomicView(event) {\n  if (event?.detail?.view === 'economic' || location.hash === '#economic-charts') initialize().catch(console.error);\n}\nwindow.addEventListener('macrowatch:dashboard-view-changed', initializeForEconomicView);\ndocument.addEventListener('DOMContentLoaded', () => { if (location.hash === '#economic-charts') initializeForEconomicView(); });"
manager = replace_once(manager, old_manager_init, new_manager_init, 'series manager lazy initialization')
write('assets/js/charts/economic-series-manager.js', manager)

# 5) Preserve the economic visual design only while that dashboard panel is active.
css = read('assets/css/economic-charts.css')
css += "\nbody.dashboard-economic-view #app-shell{max-width:1600px}\n.economic-dashboard-panel{box-sizing:border-box;width:100%;min-width:0;background:#f3f4f6;color:#111827;font-family:Pretendard,system-ui,sans-serif;border-radius:14px;overflow:hidden}\n.economic-dashboard-panel .economic-main{padding:20px}\n@media(max-width:850px){.economic-dashboard-panel .economic-main{padding:10px}}\n"
write('assets/css/economic-charts.css', css)

# 6) The old URL is now compatibility-only, not a second app.
redirect = '''<!DOCTYPE html>\n<html lang="ko">\n<head>\n  <meta charset="UTF-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n  <title>경제지표 챠트 · MacroWatch</title>\n  <meta http-equiv="refresh" content="0; url=index.html#economic-charts">\n  <script>window.location.replace('index.html#economic-charts');</script>\n</head>\n<body><a href="index.html#economic-charts">경제지표 챠트로 이동</a></body>\n</html>\n'''
write('economic-charts.html', redirect)

# 7) Remove the now-useless separate-page Supabase capture helper.
capture = ROOT / 'assets/js/core/economic-supabase-capture.js'
if capture.exists():
    capture.unlink()

# 8) Update focused contracts only; do not weaken unrelated tests.
test_actions = read('tests/test_economic_account_actions.js')
write('tests/test_economic_account_actions.js', '''const assert = require('node:assert/strict');\nconst fs = require('node:fs');\nconst path = require('node:path');\nconst root = path.join(__dirname, '..');\nconst indexHtml = fs.readFileSync(path.join(root, 'index.html'), 'utf8');\nconst legacyHtml = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');\nconst dashboard = fs.readFileSync(path.join(root, 'assets/js/dashboard/script.js'), 'utf8');\n\nassert.match(indexHtml, /data-dashboard-view="economic"/);\nassert.match(indexHtml, /data-dashboard-panel="economic"/);\nassert.match(indexHtml, /id="profile-button"/);\nassert.match(indexHtml, /id="admin-page-link"/);\nassert.match(indexHtml, /id="logout-button"/);\nassert.match(dashboard, /economic: '#economic-charts'/);\nassert.match(legacyHtml, /index\.html#economic-charts/);\nassert.doesNotMatch(legacyHtml, /profile-button|admin-page-link|logout-button|auth\.js|economic-supabase-capture/);\nassert.ok(!fs.existsSync(path.join(root, 'assets/js/core/economic-supabase-capture.js')));\nconsole.log('economic charts are one dashboard menu, not a separate app: ok');\n''')

write('tests/test_economic_account_ui.js', '''const assert = require('node:assert/strict');\nconst fs = require('node:fs');\nconst path = require('node:path');\nconst root = path.join(__dirname, '..');\nconst html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');\nconst css = fs.readFileSync(path.join(root, 'assets/css/economic-charts.css'), 'utf8');\n\nassert.equal((html.match(/id="profile-modal"/g) || []).length, 1);\nassert.equal((html.match(/id="account-delete-modal"/g) || []).length, 1);\nassert.match(html, /id="economic-dashboard-panel"[^>]*data-dashboard-panel="economic"/);\nassert.match(css, /body\.dashboard-economic-view #app-shell\{max-width:1600px\}/);\nconsole.log('economic dashboard reuses the one main account modal and preserves its own layout: ok');\n''')

# Update the dashboard menu-count contract from five to six without changing unrelated assertions.
test_dashboard = read('tests/test_dashboard.js')
test_dashboard = test_dashboard.replace("test('모바일 대시보드는 기존 분석 결과를 다섯 개 앱 메뉴로 재구성한다'", "test('모바일 대시보드는 기존 분석 결과를 여섯 개 앱 메뉴로 재구성한다'", 1)
test_dashboard = test_dashboard.replace("for (const view of ['overview', 'policy', 'earnings', 'stress', 'tracker']) {", "for (const view of ['overview', 'policy', 'earnings', 'stress', 'tracker', 'economic']) {", 1)
write('tests/test_dashboard.js', test_dashboard)

print('economic menu migration complete')
