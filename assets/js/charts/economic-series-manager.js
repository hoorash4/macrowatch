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
  {code:'KR_CORP_REHAB',title:'한국 법인회생 신청건수',frequency:'월별',category:'기업신용'},
  {code:'RRP',title:'미 연준 역레포 잔고',frequency:'일별',category:'유동성'},
  {code:'TGA',title:'미 재무부 TGA 잔고',frequency:'주별',category:'유동성'}
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
  document.body.append(shell);
  shell.querySelectorAll('[data-close-existing-series]').forEach(node => node.addEventListener('click', closeModal));
  $('economic-existing-series-form').addEventListener('submit', saveSelected);
  modal = shell;
  return shell;
}

async function preferenceRow() {
  const {data, error} = await client
    .from('economic_chart_preferences')
    .select('series_order,hidden_series,horizontal_lines')
    .eq('user_id', user.id)
    .maybeSingle();
  if (error) throw error;
  return data || {series_order:{}, hidden_series:[], horizontal_lines:{}};
}

function groupedMissing(hiddenCodes) {
  const hidden = new Set(hiddenCodes);
  const groups = new Map();
  for (const item of SERIES_CATALOG) {
    if (!hidden.has(item.code)) continue;
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  }
  return groups;
}

async function openModal() {
  if (!client || !user) return;
  buildModal();
  const list = $('economic-existing-series-list');
  const submit = $('economic-existing-series-submit');
  list.innerHTML = '<div class="economic-existing-series-empty">불러오는 중</div>';
  submit.disabled = true;
  modal.hidden = false;
  try {
    const row = await preferenceRow();
    const hidden = Array.isArray(row.hidden_series) ? row.hidden_series : [];
    const groups = groupedMissing(hidden);
    list.replaceChildren();
    if (!groups.size) {
      list.innerHTML = '<div class="economic-existing-series-empty">현재 목록에서 빠져있는 지표가 없습니다.</div>';
      return;
    }
    for (const [category, items] of groups) {
      const section = document.createElement('div');
      section.className = 'economic-existing-series-group';
      const title = document.createElement('div');
      title.className = 'economic-existing-series-group-title';
      title.textContent = category;
      section.append(title);
      for (const item of items) {
        const label = document.createElement('label');
        label.className = 'economic-existing-series-option';
        label.innerHTML = `<input type="checkbox" name="series_code" value="${item.code}"><span><strong>${item.title}</strong><small>${item.frequency}</small></span>`;
        section.append(label);
      }
      list.append(section);
    }
    list.querySelectorAll('input[type="checkbox"]').forEach(input => input.addEventListener('change', () => {
      submit.disabled = !list.querySelector('input[type="checkbox"]:checked');
    }));
  } catch (error) {
    console.error(error);
    list.innerHTML = `<div class="economic-existing-series-empty">지표 목록을 불러오지 못했습니다.<small>${error?.message || '조회 오류'}</small></div>`;
  }
}

function closeModal() {
  if (modal) modal.hidden = true;
}

async function saveSelected(event) {
  event.preventDefault();
  if (!client || !user) return;
  const selected = new Set([...$('economic-existing-series-list').querySelectorAll('input[type="checkbox"]:checked')].map(input => input.value));
  if (!selected.size) return;
  const submit = $('economic-existing-series-submit');
  submit.disabled = true;
  try {
    const row = await preferenceRow();
    const hidden = (Array.isArray(row.hidden_series) ? row.hidden_series : []).filter(code => !selected.has(code));
    const payload = {
      user_id: user.id,
      series_order: row.series_order || {},
      hidden_series: hidden,
      horizontal_lines: row.horizontal_lines || {},
      updated_at: new Date().toISOString()
    };
    const {error} = await client.from('economic_chart_preferences').upsert(payload, {onConflict:'user_id'});
    if (error) throw error;
    closeModal();
    location.reload();
  } catch (error) {
    console.error(error);
    submit.disabled = false;
    const note = $('economic-note');
    if (note) note.textContent = `지표 추가 오류: ${error?.message || '알 수 없는 오류'}`;
  }
}

function moveLiquidityLast(root) {
  const groups = [...root.querySelectorAll(':scope > .economic-series-group')];
  const liquidity = groups.find(group => group.dataset.category === '유동성');
  if (!liquidity) return;
  const add = $('economic-existing-series-add');
  if (add) {
    if (liquidity.nextElementSibling !== add) root.insertBefore(liquidity, add);
  } else if (root.lastElementChild !== liquidity) {
    root.append(liquidity);
  }
}

function ensureAddButton() {
  const root = $('economic-series-list');
  if (!root) return;
  root.querySelectorAll('.economic-series-restore').forEach(node => node.remove());
  let button = $('economic-existing-series-add');
  if (!button) {
    button = document.createElement('button');
    button.type = 'button';
    button.id = 'economic-existing-series-add';
    button.className = 'economic-existing-series-add';
    button.textContent = '+ 기존 지표 추가';
    button.addEventListener('click', openModal);
    root.append(button);
  } else if (button.parentElement !== root) {
    root.append(button);
  }
  moveLiquidityLast(root);
  if (root.lastElementChild !== button) root.append(button);
}

async function initialize() {
  if (!client) return;
  buildModal();
  const {data} = await client.auth.getSession();
  user = data.session?.user || null;
  if (!user) return;
  ensureAddButton();
  const root = $('economic-series-list');
  if (root) {
    observer = new MutationObserver(ensureAddButton);
    observer.observe(root, {childList:true});
  }
}

document.addEventListener('DOMContentLoaded', initialize);
})();