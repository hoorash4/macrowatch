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
  for (const filename of ['frontend-core.js', 'script.js', 'dashboard-charts.js']) {
    const source = fs.readFileSync(path.join(__dirname, '..', filename), 'utf8');
    vm.runInContext(source, context, { filename });
  }
  return context;
}

const dashboard = loadDashboardScript();

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

test('분석 카드 헤더와 안내 문구는 공통 규격을 사용한다', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');
  assert.equal((html.match(/class="[^"]*analysis-card-header(?:\s|"|[^"]*)/g) || []).length, 8);
  assert.equal((html.match(/<p class="analysis-card-description(?:\s|--)/g) || []).length, 18);
  assert.doesNotMatch(html, /analysis-card-header-flush/);
  assert.doesNotMatch(html, /analysis-card-description[^">]*(?:text-slate-|text-\[#[0-9a-fA-F])/);
  assert.match(styles, /--analysis-card-description-color:\s*#64748b/);
  assert.match(html, /<header class="analysis-card-header dashboard-tracker-heading">/);
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
  assert.match(workflow, /40 6 \* \* 1-5/);
  assert.match(charts, /<span>순위<\/span><span>변동<\/span><span>섹터<\/span><span>연속<\/span>/);
  assert.match(charts, /sector-flow-rank/);
  assert.match(charts, /sector-flow-change/);
  assert.match(charts, /sector-flow-returns/);
  assert.match(styles, /grid-template-columns:1\.65rem 2\.15rem minmax\(0,1fr\) 2rem/);
  assert.match(styles, /grid-column:3 \/ 5/);
  assert.match(script, /Math\.floor\(\(weekStart\.getDate\(\) - 1\) \/ 7\) \+ 1/);
  assert.match(charts, /setSectorWeekHeading\(card, week, week === weeks\.at\(-1\)\)/);
  assert.match(charts, /sector-return-positive/);
  assert.match(charts, /sector-return-negative/);
  assert.match(charts, /sector-flow-change-new/);
  assert.match(charts, /sector-flow-change-move/);
  assert.match(styles, /sector-flow-change-new[\s\S]*font-size:\.42rem/);
  assert.match(styles, /sector-flow-change-move[\s\S]*font-size:\.60rem/);
  assert.match(styles, /sector-return-positive \{ color:#d5483f/);
  assert.match(styles, /sector-return-negative \{ color:#2870ba/);
  assert.match(charts, /sectorReturn\(row\.weekly_return_pct\).*sectorReturn\(row\.cumulative_return_pct\)/s);
  assert.match(charts, /absolute < 10[\s\S]*number\.toFixed\(2\)[\s\S]*absolute < 100[\s\S]*number\.toFixed\(1\)[\s\S]*Math\.trunc\(number\)\.toString\(\)/);
  assert.doesNotMatch(charts, /오늘 (시가|종가) 기준/);
  assert.match(charts, /<small>누적<\/small>/);
  assert.doesNotMatch(charts, /<small>4주 누적<\/small>/);
  assert.match(html, /누적은 해당 주까지 4주간 수익률 \| 매 영업일 시가·종가 반영/);
  assert.match(html, /각 주를 끝점으로 한 최근 4주 수익률/);
  assert.doesNotMatch(html, /오전 9시 10분 시가 · 오후 3시 40분 종가/);
  assert.doesNotMatch(charts, /오후 3시 40분/);
});
