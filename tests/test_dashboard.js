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
  const source = fs.readFileSync(path.join(__dirname, '..', 'script.js'), 'utf8');
  vm.runInContext(source, context, { filename: 'script.js' });
  return context;
}

const dashboard = loadDashboardScript();

test('뉴스 표시일은 저장일보다 하루 앞선 날짜를 사용한다', () => {
  assert.equal(dashboard.window.MacroWatchDashboard.utils.formatNewsDate('2026-08-26'), '8/25');
  assert.equal(dashboard.window.MacroWatchDashboard.utils.formatNewsDate('invalid'), '—');
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
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  assert.match(chart, />\$\{year\}<\/text>/);
  assert.match(chart, /data-policy-cursor-period/);
  assert.match(chart, /data-policy-cursor-action/);
  assert.match(chart, /년 \$\{String\(row\.meeting_date\)\.slice\(5, 7\)\}월/);
  assert.match(chart, /TEN_YEARS_MS/);
  assert.match(chart, /frame\.scrollLeft = frame\.scrollWidth - frame\.clientWidth/);
  assert.match(chart, /\.select\('meeting_date,action,change_bps,policy_index,final_event_score'\)/);
  assert.match(html, /rounded-xl border border-slate-200 bg-slate-50 p-3[\s\S]*id="policy-signal-chart"/);
});
