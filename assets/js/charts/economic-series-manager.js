(() => {
'use strict';

const cfg = window.MACROWATCH_CONFIG || {};
const client = window.supabase?.createClient(cfg.supabaseUrl, cfg.supabasePublishableKey);

// Keep this catalog aligned with SERIES in economic-charts.js. A contract test guards drift.
const SERIES_CATALOG = [
  {code:'US2Y',title:'미국채 2년',frequency:'일별',category:'금리 · 신용'},
  {code:'US10Y',title:'미국채 10년',frequency:'일별',category:'금리 · 신용'},
  {code:'US10Y_REAL',title:'미국채 10년 실질금리',frequency:'일별',category:'금리 · 신용'},
  {code:'US10Y2Y',title:'미국 10Y-2Y 스프레드',frequency:'일별',category:'금리 · 신용'},
  {code:'HY_OAS',title:'미국 하이일드 OAS',frequency:'일별',category:'금리 · 신용'},
  {code:'EM_OAS',title:'이머징 채권 OAS',frequency:'일별',category:'금리 · 신용'},
  {code:'KR3Y',title:'국고채 3년',frequency:'일별',category:'금리 · 신용'},
  {code:'KR10Y',title:'국고채 10년',frequency:'일별',category:'금리 · 신용'},
  {code:'KR10Y3Y',title:'국고채 10Y-3Y 스프레드',frequency:'일별',category:'금리 · 신용'},
  {code:'KOSPI_PER',title:'KOSPI PER',frequency:'일별',category:'밸류에이션'},
  {code:'KOSPI_PBR',title:'KOSPI PBR',frequency:'일별',category:'밸류에이션'},
  {code:'WTI',title:'WTI 유가',frequency:'일별',category:'시장가격'},
  {code:'USDKRW',title:'원/달러 환율',frequency:'일별',category:'시장가격'},
  {code:'RRP',title:'미 연준 역레포 잔고',frequency:'일별',category:'유동성'},
  {code:'TGA',title:'미 재무부 TGA 잔고',frequency:'주별',category:'유동성'},
  {code:'REDBOOK',title:'Redbook Index',frequency:'주별',category:'고빈도 경기'},
  {code:'WEI',title:'미국 주간 경제 지수',frequency:'주별',category:'고빈도 경기'},
  {code:'EMRATIO',title:'미국 인구대비 고용률',frequency:'월별',category:'고빈도 경기'},
  {code:'US_RETAIL_SALES',title:'미국 소매판매 YoY',frequency:'월별',category:'고빈도 경기'},
  {code:'KR_EXPORT_DAILY_AVG',title:'한국 일평균 수출',frequency:'10일 구간',category:'고빈도 경기'},
  {code:'US_SBDI_31_180',title:'미국 연체율',frequency:'월별',category:'기업신용'},
  {code:'US_SBDFI',title:'미국 채무불이행률',frequency:'월별',category:'기업신용'},
  {code:'US_COMMERCIAL_CH11',title:'미국 기업 회생 신청건수',frequency:'월별',category:'기업신용'},
  {code:'KR_CORP_DELINQ',title:'한국 기업대출 연체율',frequency:'월별',category:'기업신용'},
  {code:'KR_DEFAULT_COMPANIES',title:'한국 부도업체수',frequency:'월별',category:'기업신용'},
  {code:'KR_CORP_REHAB',title:'한국 법인회생 신청건수',frequency:'월별',category:'기업신용'}
];

let user = null;
let modal = null;
let observer = null;

const $ = id => document.getElementById(id);

function buildModal() {
  if ($('economic-existing-series-modal')) return $('economic-existing-series-modal');
  const shell = document.createElement('div');
  shell.id = 'economic-existing-series-modal';
  shell.className = 'economic-modal';
  shell.hidden = true;
  shell.innerHTML = `
    <div class="economic-modal-backdrop" data-close-existing-series></div>
    <form id="economic-existing-series-form" class="economic-modal-card economic-existing-series-card">
      <div class="economic-modal-head">
        <div><strong>기존 지표 추가</strong><small>현재 목록에서 빠져있는 지표만 표시합니다.</small></div>
        <button type="button" data-close-existing-series aria-label="닫기">×</button>
      </div>
      <div id="economic-existing-series-list" class="economic-existing-series-list"></div>
      <p class="economic-modal-help">목록에서 삭제한 지표도 데이터 수집과 DB 적재는 계속됩니다. 여기서는 차트 목록 표시 여부만 변경합니다.</p>
      <div class="economic-modal-actions economic-existing-series-actions">
        <span></span><span></span><button type="button" data-close-existing-series>취소</button><button type="submit" id="economic-existing-series-submit" class="primary">추가</button>
      </div>
    </form>`;
  document.body.appendChild(shell);
  shell.querySelectorAll('[data-close-existing-series]').forEach(button => {
    button.addEventListener('click', closeModal);
  });
  $('economic-existing-series-form').addEventListener('submit', addSelected);
  return shell;
}

function openModal() {
  modal = buildModal();
  renderChoices().catch(showError);
  modal.hidden = false;
}

function closeModal() {
  if (modal) modal.hidden = true;
}

function showError(error) {
  const root = $('economic-existing-series-list');
  if (root) root.textContent = `불러오기 오류: ${error?.message || error}`;
}

async function preferences() {
  const { data, error } = await client
    .from('economic_chart_preferences')
    .select('hidden_series')
    .eq('user_id', user.id)
    .maybeSingle();
  if (error) throw error;
  return Array.isArray(data?.hidden_series) ? data.hidden_series : [];
}

async function renderChoices() {
  const hidden = new Set(await preferences());
  const root = $('economic-existing-series-list');
  root.replaceChildren();
  let count = 0;
  for (const item of SERIES_CATALOG) {
    if (!hidden.has(item.code)) continue;
    count += 1;
    const label = document.createElement('label');
    label.className = 'economic-existing-series-item';
    label.innerHTML = `<input type="checkbox" value="${item.code}"><span><strong>${item.title}</strong><small>${item.category} · ${item.frequency}</small></span>`;
    root.appendChild(label);
  }
  if (!count) root.textContent = '추가할 수 있는 숨김 지표가 없습니다.';
}

async function addSelected(event) {
  event.preventDefault();
  const selected = new Set(
    [...$('economic-existing-series-list').querySelectorAll('input[type="checkbox"]:checked')]
      .map(input => input.value)
  );
  if (!selected.size) return;
  const hidden = await preferences();
  const next = hidden.filter(code => !selected.has(code));
  const { error } = await client
    .from('economic_chart_preferences')
    .upsert({ user_id: user.id, hidden_series: next, updated_at: new Date().toISOString() }, { onConflict: 'user_id' });
  if (error) throw error;
  location.reload();
}

function ensureButton() {
  if ($('economic-existing-series-open')) return;
  const host = $('economic-series-actions');
  if (!host) return;
  const button = document.createElement('button');
  button.id = 'economic-existing-series-open';
  button.type = 'button';
  button.className = 'economic-series-restore';
  button.textContent = '+ 기존 지표 추가';
  button.addEventListener('click', openModal);
  host.appendChild(button);
}

function startObserver() {
  if (observer || !document.body) return;
  observer = new MutationObserver(ensureButton);
  observer.observe(document.body, { childList: true, subtree: true });
  ensureButton();
}

document.addEventListener('DOMContentLoaded', async () => {
  if (!client) return;
  const { data } = await client.auth.getSession();
  user = data.session?.user || null;
  if (!user) return;
  startObserver();
});
})();
