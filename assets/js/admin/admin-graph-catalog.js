(() => {
  'use strict';

  const MENUS = Object.freeze([
    {
      id: 'overview', label: '플로우 분석', icon: 'fa-chart-simple', accent: 'text-cyan-400',
      charts: [
        {
          title: '주식투자 매력 흐름', cadence: '주간 · 4주 평균',
          series: ['한국(KOSPI 100)', '미국(S&P 100)'],
          components: ['이익수익률 − 국채 10년물 금리의 수준 70%', '수익률 격차의 13주 변화 30%', '최근 5년 백분위 점수 산출 후 4주 평균'],
        },
      ],
    },
    {
      id: 'policy', label: '통화정책 시그널', icon: 'fa-landmark', accent: 'text-sky-400',
      charts: [
        {
          title: '통화정책 시그널', cadence: 'FOMC 회의 단위',
          series: ['정책 스트레스'],
          components: ['금리 결정과 정책 배경', '결정의 연속 회차와 추세 상태', '50bp 이상 변동·긴급회의·장기 전고점 도달 보정', '회의별 최종 점수를 누적한 정책지수'],
        },
        {
          title: '시장 내재 정책금리 기대', cadence: '일간 · 5영업일 평균',
          series: ['일간값', '5영업일 평균'],
          components: ['미국 국채 3개월물 − 유효연방기금금리 70%', '미국 국채 2년물 − 유효연방기금금리 30%'],
        },
        {
          title: '통합물가지수와 금리', cadence: '월간 · 최신월 잠정치',
          series: ['헤드라인', '헤드라인 잠정치', '코어', '코어 잠정치', '기준금리', '미국 10년물 금리(5일 평균)', '헤드라인 실질금리', '코어 실질금리'],
          components: ['PCE 60% + 주거비 조정 CPI 30% + 소비자물가로 정렬한 PPI 10%', '헤드라인·코어를 같은 방식으로 각각 산출', '잠정치는 CPI·PCE 나우캐스트와 에너지·식품·산업 원자재 변동을 반영', '실질금리 = 명목 기준금리 − 통합물가'],
        },
      ],
    },
    {
      id: 'earnings', label: '기업 이익 시그널', icon: 'fa-chart-column', accent: 'text-orange-400',
      charts: [
        {
          title: '시총 상위 100 이익 모멘텀', cadence: '분기',
          series: ['영업이익', '순이익', '잠정치'],
          components: ['합산 영업이익·순이익', '이익률', '전년동기 대비 이익 증가율', '계절조정 전분기 대비 이익 증가율', 'KOSPI 100·KOSDAQ 100·S&P 100·NASDAQ 100 선택'],
        },
        {
          title: '개별 기업 이익 모멘텀', cadence: '분기',
          series: ['영업이익', '순이익', '잠정치'],
          components: ['기업별 영업이익·순이익', '이익률', '전년동기 대비 이익 증가율', '계절조정 전분기 대비 이익 증가율', '시장 선택 후 기업 검색'],
        },
      ],
    },
    {
      id: 'credit', label: '미국 스트레스 지수', icon: 'fa-wave-square', accent: 'text-rose-400',
      charts: [
        {
          title: '미국 시장 스트레스 지수', cadence: '주간',
          series: ['US-MSI', 'US-MSI 잠정치', 'S&P 500 주간 종가', '선행 긴장 시그널', '신용·위험 합성 시그널'],
          components: ['하이일드 OAS 20%', 'NFCI 신용 지수 20%', 'NFCI 위험 지수 20%', '단기자금 스프레드 20%', 'NFCI 비금융 레버리지 지수 20%', '보조지표는 주간 변화의 4주 평균'],
        },
        {
          title: '미국 신용위험 추이', cadence: '월간',
          series: ['하이일드 스프레드', '금융 신용여건', '기업 파산보호 신청(3개월 평균)', '각 최신값'],
          components: ['하이일드 OAS', 'NFCI 신용 지수', '미국 기업 파산보호 신청 건수의 3개월 평균'],
        },
        {
          title: '미국 중소기업 위험지수', cadence: '월간',
          series: ['중소기업 위험지수', 'NFIB 소기업낙관지수(역)'],
          components: ['NFIB 차입난이도 60%', 'NFIB 매출전망 40%'],
        },
        {
          title: '미국 주식시장 자금환경', cadence: '주간',
          series: ['현재 우호도', '개선·악화 방향'],
          components: ['미국 10년 실질금리 25%', 'NFCI 신용여건 25%', '연준자산·TGA·역레포 기반 순유동성 변화 25%', 'SOFR·IORB 기반 자금조달 스프레드 25%'],
        },
      ],
    },
    {
      id: 'korea', label: '한국 스트레스 지수', icon: 'fa-chart-line', accent: 'text-amber-400',
      charts: [
        {
          title: '한국 시장 스트레스 지수', cadence: '월간 · 시장자료는 주간',
          series: ['K-MSI', 'K-MSI 잠정치', '코스피 주간 종가', '한국은행 FSI', '선행 긴장 시그널'],
          components: ['한국은행 금융스트레스지수(FSI) 70%', '시장 구성지수 30%: AA− 회사채−국고채 3년, BBB−−AA−, CP 91일−CD 91일, KORIBOR 3개월−KOFR 동일가중', '보조지표는 회사채·단기자금 스프레드 주간 변화의 4주 평균'],
        },
        {
          title: '한국 중소기업 위험지수', cadence: '월간 · 최근월 잠정치',
          series: ['중소기업 위험지수', '중소기업 위험지수 잠정치', '중소기업 경기전망 SBHI(역)'],
          components: ['중소기업 자금사정 전망 35%', '계절조정 가동률 30%', '중소기업대출 1개월 이상 연체율 35%', '미발표 구성값은 직전 확인값으로 이어 계산'],
        },
        {
          title: '외국인 자금 유출입 강도', cadence: '일간',
          series: ['일별 합성지표', '10영업일 누적 합성지표'],
          components: ['코스피 외국인 순매수액 ÷ 거래대금', '원화 강도(원/달러 환율 변화의 반대 방향)', '두 항목의 인과적 표준점수를 동일가중'],
        },
        {
          title: '한국 주식시장 자금환경', cadence: '주간',
          series: ['현재 우호도', '개선·악화 방향'],
          components: ['콜금리·기준금리 기반 자금조달 여건 25%', 'M2·Lf 기반 유동성 증가 25%', '외국인 주식자금 흐름 25%', '원화 강도 25%'],
        },
      ],
    },
    {
      id: 'em', label: '이머징 스트레스 지수', icon: 'fa-earth-americas', accent: 'text-teal-400',
      charts: [
        {
          title: '이머징 시장 스트레스 지수', cadence: '주간',
          series: ['EM-MSI', 'EM-MSI 잠정치', 'EEM 주간 종가'],
          components: ['이머징 달러지수 45%', '이머징 하이일드 OAS 27.5%', '이머징 테일리스크 회사채 OAS 27.5%', '이머징 주식 변동성은 본지수 가중치에서 제외'],
        },
        {
          title: '이머징 자금 유입 여건', cadence: '일간 · 5영업일 평균',
          series: ['일간값', '5영업일 평균', '5영업일 평균 잠정치'],
          components: ['이머징 달러지수', '미국 10년 실질금리', '미국 하이일드 OAS', 'NFCI', '네 지표의 인과적 표준점수를 동일가중하고 방향 반전'],
        },
      ],
    },
  ]);
  const DISPLAY_MENUS = Object.freeze([
    ...MENUS.filter((menu) => !['credit', 'korea', 'em'].includes(menu.id)),
    {
      id: 'stress', label: '스트레스 지수', icon: 'fa-wave-square', accent: 'text-rose-400',
      charts: MENUS.filter((menu) => ['credit', 'korea', 'em'].includes(menu.id)).flatMap((menu) => menu.charts),
    },
  ]);

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character]));
  }

  function chartMarkup(chart) {
    const series = chart.series.map((label) => `<span class="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] text-slate-300">${escapeHtml(label)}</span>`).join('');
    const components = chart.components.map((component) => `<li class="flex gap-2"><span class="mt-[.45rem] h-1.5 w-1.5 shrink-0 rounded-full bg-slate-600"></span><span>${escapeHtml(component)}</span></li>`).join('');
    return `<article class="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
      <div class="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-3"><h4 class="font-bold text-slate-100">${escapeHtml(chart.title)}</h4><span class="shrink-0 text-xs text-slate-500">${escapeHtml(chart.cadence)}</span></div>
      <div class="mt-3"><p class="mb-2 text-[11px] font-bold uppercase tracking-[.12em] text-slate-500">표시 항목</p><div class="flex flex-wrap gap-1.5">${series}</div></div>
      <div class="mt-3 border-t border-slate-800 pt-3"><p class="mb-2 text-[11px] font-bold uppercase tracking-[.12em] text-slate-500">산출 구성</p><ul class="space-y-1.5 text-xs leading-relaxed text-slate-400">${components}</ul></div>
    </article>`;
  }

  function menuMarkup(menu) {
    const charts = menu.charts.map(chartMarkup).join('');
    return `<details class="group overflow-hidden rounded-xl border border-slate-800 bg-slate-950/40">
      <summary class="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 hover:bg-slate-800/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500">
        <span class="flex items-center gap-2 font-bold text-slate-100"><i class="fa-solid ${escapeHtml(menu.icon)} w-5 ${escapeHtml(menu.accent)}" aria-hidden="true"></i>${escapeHtml(menu.label)}</span>
        <span class="flex items-center gap-3 text-xs text-slate-500"><span>${menu.charts.length}개 그래프</span><i class="fa-solid fa-chevron-down transition-transform group-open:rotate-180" aria-hidden="true"></i></span>
      </summary>
      <div class="grid grid-cols-1 gap-3 border-t border-slate-800 p-3 xl:grid-cols-2">${charts}</div>
    </details>`;
  }

  function render(documentRef = document) {
    const container = documentRef.getElementById('graph-component-catalog');
    if (!container) return false;
    container.innerHTML = DISPLAY_MENUS.map(menuMarkup).join('');
    const graphCount = DISPLAY_MENUS.reduce((sum, menu) => sum + menu.charts.length, 0);
    const summary = documentRef.getElementById('graph-catalog-summary');
    if (summary) summary.textContent = `${DISPLAY_MENUS.length}개 메뉴 · ${graphCount}개 그래프`;
    return true;
  }

  const api = Object.freeze({ MENUS: DISPLAY_MENUS, render });
  window.MacroWatchAdminGraphCatalog = api;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => render());
  else render();
})();
