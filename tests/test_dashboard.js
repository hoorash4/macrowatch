const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');

// 브라우저 전역을 최소한으로 흉내 내어 script.js의 순수 보조 함수만 검증한다.
// 실제 DOM 렌더링은 건드리지 않으며, 리팩터링 전후 계산 결과가 같은지 확인한다.
function loadDashboardScript() {
  const context = {
    console,
    URL,
    URLSearchParams,
    Intl,
    Date,
    Math,
    Number,
    String,
    Array,
    Set,
    Map,
    Promise,
    CustomEvent: class CustomEvent {
      constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail;
      }
    },
    setTimeout: () => 0,
    clearTimeout: () => {},
    window: {
      MACROWATCH_CONFIG: { supabaseUrl: 'https://example.supabase.co', supabasePublishableKey: 'public-key' },
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => {},
      alert: () => {},
      location: { hash: '', pathname: '/', replace: () => {} },
      history: { replaceState: () => {} },
    },
    document: {
      addEventListener: () => {},
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
      body: { classList: { add: () => {}, remove: () => {}, toggle: () => {} } },
      documentElement: { scrollTop: 0 },
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  // 운영 HTML과 같은 순서로 공통 기반 → 페이지 셸/추적 → 차트 모듈을 불러온다.
  for (const filename of ['frontend-core.js', 'indicator-terms.js', 'script.js', 'dashboard-charts.js']) {
    const source = fs.readFileSync(path.join(__dirname, '..', filename), 'utf8');
    vm.runInContext(source, context, { filename });
  }
  return context;
}

const dashboard = loadDashboardScript();

test('지표 코드 검색은 밝은 입력 표면과 단독 국채 만기 표현을 지원한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  const queries = dashboard.window.MacroWatchDashboard.utils.buildFredSearchTerms('10년물');

  assert.match(html, /id="indicator-search-query"[^>]*class="input-surface-light/);
  assert.match(styles, /#tab-content-tracker input\.input-surface-light\s*\{[\s\S]*?color:#0f172a;[\s\S]*?-webkit-text-fill-color:#0f172a;/);
  assert.ok(queries.includes('10-Year Treasury Constant Maturity Rate'));
});

test('지표 등록 오류는 브라우저 경고창 대신 공용 중앙 모달을 사용한다', () => {
  const script = fs.readFileSync(path.join(__dirname, '..', 'script.js'), 'utf8');
  const start = script.indexOf('async function handleAddTarget(e)');
  const end = script.indexOf('// ===== 지표 수정', start);
  const handler = script.slice(start, end);

  assert.ok(start >= 0 && end > start);
  assert.match(handler, /showCenteredNotice\('지표 등록 실패'/);
  assert.doesNotMatch(handler, /window\.alert\('등록 실패:/);
});

test('뉴스 표시일은 저장일보다 하루 앞선 날짜를 사용한다', () => {
  assert.equal(dashboard.window.MacroWatchDashboard.utils.formatNewsDate('2026-08-26'), '8/25');
  assert.equal(dashboard.window.MacroWatchDashboard.utils.formatNewsDate('invalid'), '—');
});

test('결정적 뉴스는 수집일 기준 한국시간 월요일부터 일요일까지 누적한다', () => {
  const aggregate = dashboard.window.MacroWatchChartUtils.aggregateWeeklyDecisiveNews;
  const result = aggregate([
    { article_date: '2026-08-23', decisive_news_count: 9, decisive_news_keywords: ['지난주'] },
    { article_date: '2026-08-24', decisive_news_count: 1, decisive_news_keywords: ['신용경색'] },
    { article_date: '2026-08-27', decisive_news_count: 2, decisive_news_keywords: ['신용경색', '환율'] },
    { article_date: '2026-08-31', decisive_news_count: 7, decisive_news_keywords: ['다음주'] },
  ], new Date('2026-08-27T03:00:00Z'));
  assert.equal(result.count, 3);
  assert.deepEqual([...result.keywords], ['신용경색', '환율']);
});

test('결정적 뉴스 요약은 설명 아래에서 건수와 키워드를 나란히 배치한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  assert.match(html, /decisive-news-description[\s\S]*decisive-news-summary-row[\s\S]*decisive-news-count-card[\s\S]*decisive-news-keyword-panel/);
  assert.match(styles, /\.decisive-news-summary-row\s*\{[\s\S]*?grid-template-columns:minmax\(13\.5rem,auto\) minmax\(0,1fr\)/);
  assert.match(html, /나열된 키워드가 포함된 뉴스를 지속적으로 관찰할 필요가 있습니다/);
  assert.match(html, /class="decisive-news-ai-note">AI의 판단이므로 실제 중요도와 다를 수 있습니다\.<\/span>/);
  assert.match(styles, /\.decisive-news-content\s*\{[\s\S]*?padding:1\.25rem 1\.5rem/);
  assert.match(styles, /\.decisive-news-count-card\s*\{[\s\S]*?align-items:center;[\s\S]*?text-align:center;/);
  assert.match(styles, /\.decisive-news-ai-note\s*\{[\s\S]*?display:block;/);
  assert.doesNotMatch(html, /표시 예시|#금융기관 부실|#신용시장 경색|#감염병 확산/);
  assert.match(html, /아직 집계된 키워드가 없습니다/);
  assert.match(fs.readFileSync(path.join(__dirname, '..', 'dashboard-charts.js'), 'utf8'), /class="decisive-news-keyword">#\$\{escapeHtml\(keyword\)\}<\/span>/);
});

test('상관계수 계산은 완전한 양·음의 관계를 보존한다', () => {
  const { calculateCorrelation } = dashboard.window.MacroWatchDashboard.utils;
  assert.equal(calculateCorrelation([[1, 2], [2, 4], [3, 6]]), 1);
  assert.equal(calculateCorrelation([[1, 6], [2, 4], [3, 2]]), -1);
  assert.equal(calculateCorrelation([[1, 1]]), null);
});

test('사용자 입력을 HTML에 안전하게 표시한다', () => {
  assert.equal(
    dashboard.window.MacroWatchDashboard.utils.escapeHtml('<a title="x">A&B</a>'),
    '&lt;a title=&quot;x&quot;&gt;A&amp;B&lt;/a&gt;',
  );
});

test('알림 조건의 사용자 표시 문구를 보존한다', () => {
  const { getConditionText } = dashboard.window.MacroWatchDashboard.utils;
  assert.equal(getConditionText('changed'), '지표값 변동 감지');
  assert.equal(getConditionText('gte'), '설정값 상향 돌파');
  assert.equal(getConditionText('custom'), 'custom');
});

test('HTML inline 이벤트가 사용하는 핸들러만 명시적으로 공개한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const handlerNames = [...html.matchAll(/on(?:click|change|submit|input|keydown)="([A-Za-z_$][\w$]*)/g)]
    .map((match) => match[1]);
  assert.ok(handlerNames.length > 0);
  for (const name of new Set(handlerNames)) {
    assert.equal(typeof dashboard.window[name], 'function', `${name} 핸들러가 공개되어야 한다`);
  }
});

test('FOMC 정책 그래프는 네 자리 연도와 커서 월 표시를 제공한다', () => {
  const chart = fs.readFileSync(path.join(__dirname, '..', 'policy-chart.js'), 'utf8');
  const main = fs.readFileSync(path.join(__dirname, '..', 'script.js'), 'utf8');
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(chart, />\$\{year\}<\/text>/);
  assert.match(chart, /data-policy-cursor-period/);
  assert.match(chart, /data-policy-cursor-action/);
  assert.match(chart, /년 \$\{String\(row\.meeting_date\)\.slice\(5, 7\)\}월/);
  assert.match(chart, /TEN_YEARS_MS/);
  assert.match(chart, /frame\.scrollLeft = frame\.scrollWidth - frame\.clientWidth/);
  assert.match(chart, /macrowatch:dashboard-view-changed/);
  assert.match(chart, /detail\?\.view !== 'policy'/);
  assert.match(main, /new CustomEvent\('macrowatch:dashboard-view-changed'/);
  assert.match(chart, /\.select\('meeting_date,action,change_bps,policy_index,final_event_score'\)/);
  assert.match(html, /rounded-xl border border-slate-200 bg-slate-50 p-3[\s\S]*id="policy-signal-chart"/);
});

test('시장 내재 정책금리 기대 그래프는 5년을 기본으로 기간별 조회를 제공한다', () => {
  const chart = fs.readFileSync(path.join(__dirname, '..', 'policy-expectation-chart.js'), 'utf8');
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(chart, /selectedYears: 5/);
  assert.match(chart, /function rowsForSelectedRange/);
  assert.match(chart, /function withFiveDayAverage/);
  assert.match(chart, /policy-expectation-line--raw/);
  assert.match(chart, /policy-expectation-line--average/);
  assert.match(chart, /5일 평균/);
  for (const range of ['1', '2', '5', '10', 'max']) assert.match(html, new RegExp(`data-policy-expectation-range="${range}"`));
  assert.match(html, /data-policy-expectation-range="5" class="is-active"/);
  assert.match(html, /policy-expectation-chart\.js\?v=4/);
});

test('분석 카드 헤더와 안내 문구는 공통 규격을 사용한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  assert.equal((html.match(/class="[^"]*analysis-card-header(?:\s|"|[^"]*)/g) || []).length, 10);
  assert.equal((html.match(/<p class="analysis-card-description(?:\s|--|")/g) || []).length, 21);
  assert.doesNotMatch(html, /analysis-card-header-flush/);
  assert.doesNotMatch(html, /analysis-card-description[^">]*(?:text-slate-|text-\[#[0-9a-fA-F])/);
  assert.match(styles, /--analysis-card-description-color:\s*#64748b/);
  assert.match(html, /<header class="analysis-card-header dashboard-tracker-heading">/);
  assert.equal((html.match(/class="analysis-card-heading-row"/g) || []).length, 10);
  assert.equal((html.match(/class="analysis-card-eyebrow analysis-card-eyebrow--/g) || []).length, 10);
  assert.doesNotMatch(html, /analysis-card-title (?:mt-|text-|font-|tracking-)/);
  assert.doesNotMatch(html, /analysis-card-description (?:mt-|text-)/);
  assert.match(styles, /\.analysis-card-title\s*\{[\s\S]*?font-size:1\.15rem;[\s\S]*?font-weight:700;/);
  assert.doesNotMatch(styles, /analysis-card-header-light \.analysis-card-title/);
});

test('공용 대화상자는 하나의 오버레이 컴포넌트와 층위 수정자만 사용한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');

  assert.equal((html.match(/class="modal-overlay[^\"]* hidden"/g) || []).length, 7);
  assert.doesNotMatch(html, /class="hidden fixed inset-0[^\"]*bg-black/);
  assert.match(styles, /\.modal-overlay:not\(\.hidden\)\s*\{\s*display:flex;/);
  assert.match(styles, /\.modal-overlay--critical\s*\{[\s\S]*?z-index:80;/);
});

test('분석 메뉴의 최상단 공간과 카드 간격은 공통 토큰을 사용한다', () => {
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  assert.match(styles, /--dashboard-panel-top-space:\s*1\.5rem/);
  assert.match(styles, /--dashboard-card-gap:\s*1\.5rem/);
  assert.match(styles, /\.dashboard-panels\s*\{[\s\S]*?gap:var\(--dashboard-card-gap\);[\s\S]*?padding-top:var\(--dashboard-panel-top-space\);/);
  assert.match(styles, /\.market-overview\s*\{[\s\S]*?gap:var\(--dashboard-card-gap\);/);
  assert.doesNotMatch(styles, /#credit-stress-dashboard\s*\{[\s\S]*?margin-bottom:4rem/);
  assert.doesNotMatch(styles, /\.market-overview-grid\s*\{[\s\S]*?padding-top:1\.5rem/);
});

test('공통 지표 추적 영역은 메뉴 카드와 구분되는 공통 토큰을 사용한다', () => {
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  assert.match(styles, /--tracker-section-separation:\s*1\.5rem/);
  assert.match(styles, /--tracker-card-accent:\s*#8aa2b4/);
  assert.match(styles, /--tracker-card-border-width:\s*2px/);
  assert.match(styles, /--tracker-card-border-top-width:\s*8px/);
  assert.match(styles, /\.dashboard-tracker-card\s*\{[\s\S]*?margin-top:var\(--tracker-section-separation\);[\s\S]*?border:var\(--tracker-card-border-width\) solid var\(--tracker-card-accent\);[\s\S]*?border-top-width:var\(--tracker-card-border-top-width\);[\s\S]*?background:var\(--tracker-card-background\);/);
});

test('지표 추적 목록의 항목 구분선은 목록 컨테이너가 한 번만 그린다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const script = fs.readFileSync(path.join(__dirname, '..', 'script.js'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');

  assert.match(html, /id="target-list" class="divide-y divide-slate-800\/80"/);
  assert.doesNotMatch(script, /data-target-container[^\n]*\bborder-(?:b|t)\b/);
  assert.doesNotMatch(script, /target-container-(?:first|last)/);
  assert.doesNotMatch(styles, /\.target-container-(?:first|last)/);
});

test('지표 순서 변경은 들어 올린 행의 중앙으로 판정하고 삽입선을 구분선 위에 표시한다', () => {
  const script = fs.readFileSync(path.join(__dirname, '..', 'script.js'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');

  assert.match(script, /function getDragPreviewCenterY\(pointerClientY\)/);
  assert.match(script, /document\.elementFromPoint\(clientX, dragCenterY\)/);
  assert.match(script, /dragCenterY < rect\.top \+ rect\.height \/ 2/);
  assert.doesNotMatch(script, /targetIndex === draggedItemIndex/);
  assert.match(styles, /--tracker-drag-source-opacity:\s*\.4/);
  assert.match(styles, /#target-list \.is-drag-source > \*\s*\{\s*opacity:var\(--tracker-drag-source-opacity\);/);
  assert.doesNotMatch(styles, /#target-list \.is-drag-source\s*\{\s*opacity:/);
  assert.doesNotMatch(styles, /\.drop-indicator-(?:before|after)\s*\{[^}]*box-shadow/);
  assert.match(styles, /--tracker-drop-indicator-height:\s*1px/);
  assert.match(styles, /\.drop-indicator-before::before,[\s\S]*?height:var\(--tracker-drop-indicator-height\);[\s\S]*?background:var\(--tracker-drop-indicator-color\);/);
});

test('관리자 뉴스 일정은 실제 워크플로 예약 시각을 안내한다', () => {
  const admin = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8');
  assert.match(admin, /매일 00:30 KST/);
  assert.match(admin, /00:50 · 01:10 자동 재시도/);
  assert.doesNotMatch(admin, /매일 05:30 KST/);
});

test('뉴스 흐름 안내는 전일 집계와 예상 완료 시점을 표시한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(html, /전일 24시간 동안 기사화된 뉴스의 긍정·부정 흐름을 분석합니다/);
  assert.match(html, /매일 오전 1시경\(KST\) 업데이트됩니다/);
  assert.doesNotMatch(html, /최근 24시간 동안 기사화된 뉴스/);
});

test('프론트엔드 공통 기반과 차트 모듈은 운영 순서로 분리되어 있다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const main = fs.readFileSync(path.join(__dirname, '..', 'script.js'), 'utf8');
  const charts = fs.readFileSync(path.join(__dirname, '..', 'dashboard-charts.js'), 'utf8');
  const coreIndex = html.indexOf('<script src="frontend-core.js');
  const authIndex = html.indexOf('<script src="auth.js');
  const mainIndex = html.indexOf('<script src="script.js');
  const chartIndex = html.indexOf('<script src="dashboard-charts.js');
  assert.ok(coreIndex < authIndex && authIndex < mainIndex && mainIndex < chartIndex);
  assert.doesNotMatch(main, /function renderMarketStressDashboard/);
  assert.match(charts, /function renderMarketStressDashboard/);
  assert.match(charts, /MacroWatchDashboard\?\.registerLoader/);
  assert.doesNotMatch(main, /function escapeHtml/);
});

test('관리자 톱니는 권한 확인 전 hidden 속성으로 감춘다', () => {
  const indexHtml = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const authJs = fs.readFileSync(path.join(__dirname, '..', 'auth.js'), 'utf8');
  const stylesCss = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  assert.match(indexHtml, /id="admin-page-link"[^>]*hidden/);
  assert.match(authJs, /link\.hidden = true/);
  assert.match(authJs, /data\?\.is_admin === true\) link\.hidden = false/);
  assert.match(stylesCss, /\.dashboard-nav-actions \[hidden\]\s*\{\s*display:none/);
});

test('뉴스 흐름 확장 그래프는 왼쪽부터 채우고 기간 버튼은 공통 스타일을 사용한다', () => {
  const charts = fs.readFileSync(path.join(__dirname, '..', 'dashboard-charts.js'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  assert.match(charts, /items-end justify-start/);
  assert.doesNotMatch(charts, /items-end justify-between/);
  assert.match(charts, /news-sentiment-graph--expanded/);
  assert.match(styles, /grid-template-columns:repeat\(30,minmax\(1\.5rem,1fr\)\)/);
  assert.match(charts, /class="news-sentiment-view-button"/);
  assert.match(charts, /news-sentiment-view-button--back/);
  assert.match(styles, /\.news-sentiment-view-button\s*\{/);
  assert.match(charts, /news-sentiment-toolbar/);
  assert.match(styles, /#news-sentiment-chart\s*\{[\s\S]*?padding-right:1\.25rem;[\s\S]*?padding-left:1\.25rem;/);
  assert.match(styles, /\.news-sentiment-toolbar\s*\{[\s\S]*?justify-content:space-between;/);
});

test('이머징 그래프의 커서 상단에는 EM-MSI 숫자만 표시한다', () => {
  const charts = fs.readFileSync(path.join(__dirname, '..', 'dashboard-charts.js'), 'utf8');
  assert.match(charts, /valueLabel\.textContent = Number\(nearest\.stress_index\)\.toFixed\(2\)/);
  assert.doesNotMatch(charts, /valueLabel\.textContent[^\n]*EM-MSI/);
  assert.doesNotMatch(charts, /valueLabel\.textContent[^\n]*EEM/);
});

test('주도섹터는 모든 주에 주간과 4주 누적 수익률을 표시한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const charts = fs.readFileSync(path.join(__dirname, '..', 'dashboard-charts.js'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  const script = fs.readFileSync(path.join(__dirname, '..', 'script.js'), 'utf8');
  const workflow = fs.readFileSync(path.join(__dirname, '..', '.github/workflows/sector-flow.yml'), 'utf8');
  assert.match(workflow, /10 0 \* \* 1-5/);
  assert.match(workflow, /30 3 \* \* 1-5/);
  assert.match(workflow, /40 6 \* \* 1-5/);
  assert.match(charts, /<span>순위<\/span><span>변동<\/span><span>섹터<\/span><span>연속<\/span>/);
  assert.match(charts, /sector-flow-rank/);
  assert.match(charts, /sector-flow-change/);
  assert.match(charts, /sector-flow-returns/);
  assert.match(styles, /grid-template-columns:1\.65rem 2\.15rem minmax\(0,1fr\) 2rem/);
  assert.match(styles, /grid-column:3 \/ 5/);
  assert.match(script, /Math\.floor\(\(weekStart\.getDate\(\) - 1\) \/ 7\) \+ 1/);
  assert.match(charts, /setSectorWeekHeading\(card, week, isLatestWeek\)/);
  assert.match(charts, /sector-return-positive/);
  assert.match(charts, /sector-return-negative/);
  assert.match(charts, /sector-flow-change-new/);
  assert.match(charts, /sector-flow-change-move/);
  assert.match(charts, /sectorRankChange\(row, showNew\)/);
  assert.match(charts, /sectorRankChange\(row, isLatestWeek\)/);
  assert.match(charts, /slice\(0, 6\)/);
  assert.match(styles, /sector-flow-change-new[\s\S]*font-size:\.42rem/);
  assert.match(styles, /sector-flow-change-move[\s\S]*font-size:\.60rem/);
  assert.match(styles, /sector-return-positive \{ color:#d5483f/);
  assert.match(styles, /sector-return-negative \{ color:#2870ba/);
  assert.match(styles, /sector-flow-returns > span:nth-child\(2\) em \{ font-weight:500; \}/);
  assert.match(charts, /sectorReturn\(row\.weekly_return_pct\).*sectorReturn\(row\.cumulative_return_pct\)/s);
  assert.match(charts, /absolute < 10[\s\S]*number\.toFixed\(2\)[\s\S]*absolute < 100[\s\S]*number\.toFixed\(1\)[\s\S]*Math\.trunc\(number\)\.toString\(\)/);
  assert.doesNotMatch(charts, /오늘 (시가|종가) 기준/);
  assert.match(charts, /<small>누적<\/small>/);
  assert.doesNotMatch(charts, /<small>4주 누적<\/small>/);
  assert.match(html, /id="sector-flow-update-note"[^>]*>매 영업일 시가·종가 반영/);
  assert.match(html, /각 주를 끝점으로 한 최근 4주 수익률/);
  assert.doesNotMatch(html, /오전 9시 10분 시가 · 오후 3시 40분 종가/);
  assert.doesNotMatch(charts, /오후 3시 40분/);
});

test('주도섹터는 이번 주와 과거 4주를 표시하고 한 주를 변동 기준으로 조회한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const charts = fs.readFileSync(path.join(__dirname, '..', 'dashboard-charts.js'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  assert.match(html, /data-sector-week-offset="0"/);
  assert.match(html, /이번 주 섹터별 주간 수익률 순위/);
  assert.match(charts, /isLatest \? '이번 주 섹터별 주간 수익률 순위'/);
  assert.match(html, /data-sector-week-offset="-4"/);
  assert.match(html, /class="sector-flow-history"/);
  assert.match(charts, /sort\(\)\.slice\(-6\)/);
  assert.match(charts, /market_sector_etf_holdings\(holding_name,weight_pct,weight_rank\)/);
  assert.match(charts, /섹터 대표 종목/);
  assert.match(charts, /sector-flow-holdings-empty/);
  assert.match(charts, /initializeSectorHoldingInteractions/);
  assert.match(charts, /pointerout/);
  assert.doesNotMatch(styles, /sector-flow-sector:hover \.sector-flow-holdings/);
  assert.doesNotMatch(charts, /weight_pct\)\.toFixed/);
  assert.match(styles, /\.sector-flow-streak \{[\s\S]*font-size:\.6rem/);
  assert.match(styles, /sector-flow-week:last-child \.sector-flow-holdings[\s\S]*right:0;[\s\S]*left:auto;/);
  assert.match(styles, /sector-flow-week \.sector-flow-holdings > b[\s\S]*width:100%;[\s\S]*background:#e7f1f8/);
  assert.match(styles, /\.sector-flow-holdings \{[\s\S]*width:9rem;/);
  assert.match(styles, /\.sector-flow-week-current > ol \{\s*grid-template-columns:1fr;/);
  assert.match(styles, /sector-flow-week-current > ol > li:last-child \{ border-bottom:0; \}/);
  assert.match(charts, /<span>대표종목<\/span><span>주간 수익률<\/span><span>4주 누적 수익률<\/span><span>랭킹 연속 유지<\/span>/);
  assert.match(charts, /function sectorTopHolding\(etf\)/);
  assert.match(charts, /isLatestWeek \? '주차' : '주'/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-columns,[\s\S]*grid-template-columns:2rem 2\.5rem minmax\(8rem,1fr\) 10\.5rem repeat\(3,7rem\)/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-returns \{ display:contents; \}/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-columns span:nth-child\(-n\+2\) \{ text-align:center; \}/);
  assert.match(charts, /isLatestWeek \? `\$\{sectorTopHolding\(etf\)\}\$\{returns\}\$\{streak\}` : `\$\{streak\}\$\{returns\}`/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-streak \{[\s\S]*font-size:\.68rem;[\s\S]*text-align:center;/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-returns > span \{\s*justify-content:center;/);
  assert.match(styles, /\.sector-flow-top-holding \{[\s\S]*text-align:center;/);
  assert.match(styles, /sector-flow-week-current header > strong \{ font-size:\.9rem; \}/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-columns \{[\s\S]*font-size:\.74rem;/);
  assert.match(styles, /\.sector-flow-week li b \{[\s\S]*width:1\.45rem;[\s\S]*min-height:1\.35rem;[\s\S]*font-size:\.66rem;/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-sector \{ font-size:\.88rem; \}/);
  assert.match(styles, /\.sector-flow-top-holding \{[\s\S]*font-size:\.78rem;[\s\S]*font-weight:500;/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-returns em \{ font-size:\.8rem; \}/);
  assert.match(styles, /sector-flow-week:not\(\.sector-flow-week-current\)[\s\S]*\.sector-flow-streak \{[\s\S]*justify-self:center;[\s\S]*text-align:center;/);
  assert.match(styles, /sector-flow-week-current > ol > li > \.sector-flow-rank,[\s\S]*sector-flow-week-current > ol > li > \.sector-flow-change \{ justify-self:center; \}/);
  assert.doesNotMatch(styles, /sector-flow-(rank|change) \{ transform:translateX/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-columns span:nth-child\(n\+4\)::before,[\s\S]*border-left:1px dashed rgba\(47,105,154,\.2\)/);
  assert.match(styles, /\.sector-flow-columns \{[\s\S]*border-bottom:1px solid rgba\(47,105,154,\.2\)/);
  assert.match(styles, /\.sector-flow-week li \{[\s\S]*border-bottom:1px dashed rgba\(47,105,154,\.16\)/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-streak \{[\s\S]*background:rgba\(47,105,154,\.07\)/);
  assert.match(styles, /\.sector-flow-top-holding \{[\s\S]*color:#7890a2;/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-streak \{\s*justify-self:center;\s*box-sizing:border-box;/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-columns span:nth-child\(4\)::before \{ display:none; \}/);
  assert.match(styles, /sector-flow-week-current > ol > li::after \{[\s\S]*right:7\.325rem;[\s\S]*border-left:1px dashed rgba\(47,105,154,\.2\)/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-returns > span:first-child em \{[\s\S]*border-radius:\.4rem;[\s\S]*text-align:center;/);
  assert.match(styles, /sector-flow-week-current \.sector-flow-returns > span:first-child em \{[\s\S]*min-width:4rem;[\s\S]*padding:\.16rem \.32rem;/);
  assert.match(styles, /span:first-child em\.sector-return-positive \{[\s\S]*background:rgba\(213,72,63,\.08\)/);
  assert.match(styles, /span:first-child em\.sector-return-negative \{[\s\S]*background:rgba\(40,112,186,\.08\)/);
  assert.match(styles, /sector-flow-week:not\(\.sector-flow-week-current\) header > strong \{ font-size:\.88rem; \}/);
  assert.match(styles, /sector-flow-week:not\(\.sector-flow-week-current\) \.sector-flow-columns \{ font-size:\.64rem; \}/);
  assert.match(styles, /sector-flow-week:not\(\.sector-flow-week-current\) \.sector-flow-sector \{ font-size:\.86rem; \}/);
  assert.match(styles, /sector-flow-week:not\(\.sector-flow-week-current\) \.sector-flow-returns \{[\s\S]*border-radius:\.42rem;[\s\S]*background:rgba\(47,105,154,\.055\)/);
  assert.match(styles, /sector-flow-week:not\(\.sector-flow-week-current\) \.sector-flow-returns \{\s*padding:\.28rem \.2rem;/);
  assert.doesNotMatch(styles, /sector-flow-week:not\(\.sector-flow-week-current\) \.sector-flow-returns > span:nth-child\(2\)::before/);
  assert.match(styles, /sector-flow-week:not\(\.sector-flow-week-current\) \.sector-flow-returns em \{ font-size:\.7rem; \}/);
  assert.match(styles, /sector-flow-week-current \{ min-width:48rem; \}/);
  assert.match(charts, /섹터 <span class="sector-flow-classification-note">\(KRX 업종 구분과 다름\)<\/span>/);
  assert.match(styles, /\.sector-flow-classification-note \{[\s\S]*font-size:\.66rem;[\s\S]*font-weight:500;/);
  assert.doesNotMatch(styles, /margin-left:\.28rem/);
});
