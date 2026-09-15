(() => {
  'use strict';
  const RATE_CATEGORY='금리',FINANCIAL_CREDIT_CATEGORY='금융신용',LEGACY_RATE_CATEGORY='금리 · 신용';
  const BUSINESS_DISTRESS_CATEGORY='기업부실',LEGACY_BUSINESS_CREDIT_CATEGORY='기업신용';
  const FREQUENCY_LABELS={D:'일별',W:'주별',T:'10일 구간',M:'월별',Q:'분기별',E:'결정일'};
  const MARKET_SCOPE=Object.freeze({
    KR:new Set(['KR_POLICY_RATE','KR_CORP_CREDIT_SPREAD','KR3Y','KR10Y','KR10Y3Y','KOSPI_PER','KOSPI_PBR','USDKRW','KR_EXPORT_DAILY_AVG','KR_CPI','KR_CORE_CPI','KR_PPI','KR_IMPORT_PRICE','KR_CORP_DELINQ','KR_DEFAULT_COMPANIES','KR_CORP_REHAB']),
    GLOBAL:new Set(['WTI','EM_OAS'])
  });
  const marketScope=code=>MARKET_SCOPE.KR.has(code)?'KR':MARKET_SCOPE.GLOBAL.has(code)?'GLOBAL':'US';
  const DEFAULT_CATEGORY_ORDER=[RATE_CATEGORY,FINANCIAL_CREDIT_CATEGORY,'밸류에이션','시장가격','고빈도 경기','물가',BUSINESS_DISTRESS_CATEGORY,'유동성'];
  const SERIES=[
    {code:'US2Y',title:'미국채 2년',frequency:'D',unit:'%',category:RATE_CATEGORY,decimals:2},
    {code:'US10Y',title:'미국채 10년',frequency:'D',unit:'%',category:RATE_CATEGORY,decimals:2},
    {code:'US10Y_REAL',title:'미국채 10년 실질금리',frequency:'D',unit:'%',category:RATE_CATEGORY,decimals:2},
    {code:'US10Y2Y',title:'미국 10Y-2Y 스프레드',frequency:'D',unit:'%p',category:RATE_CATEGORY,decimals:2},
    {code:'US_POLICY_RATE_MID',compareCode:'KR_POLICY_RATE',title:'기준금리',legendTitle:'미국 기준금리 (약 6주 간격)',compareTitle:'한국 기준금리 (1개월 간격)',frequency:'E',frequencyLabel:'미국 결정일 · 한국 1개월',unit:'%',category:RATE_CATEGORY,decimals:2,lineType:'steps',maAvailable:false},
    {code:'HY_OAS',title:'미국 하이일드 OAS',frequency:'D',unit:'%p',category:FINANCIAL_CREDIT_CATEGORY,decimals:2},
    {code:'NFCI_CREDIT',title:'미국 금융 신용여건',frequency:'W',unit:'지수',category:FINANCIAL_CREDIT_CATEGORY,decimals:3},
    {code:'NFCI_RISK',title:'NFCI 위험지수',frequency:'W',unit:'지수',category:FINANCIAL_CREDIT_CATEGORY,decimals:2},
    {code:'EM_OAS',title:'이머징 채권 OAS',frequency:'D',unit:'%p',category:FINANCIAL_CREDIT_CATEGORY,decimals:2},
    {code:'KR_CORP_CREDIT_SPREAD',title:'한국 회사채 신용스프레드',frequency:'D',unit:'%p',category:FINANCIAL_CREDIT_CATEGORY,decimals:2},
    {code:'KR3Y',title:'국고채 3년',frequency:'D',unit:'%',category:RATE_CATEGORY,decimals:2},
    {code:'KR10Y',title:'국고채 10년',frequency:'D',unit:'%',category:RATE_CATEGORY,decimals:2},
    {code:'KR10Y3Y',title:'국고채 10Y-3Y 스프레드',frequency:'D',unit:'%p',category:RATE_CATEGORY,decimals:2},
    {code:'KOSPI_PER',title:'KOSPI PER',frequency:'D',unit:'배',category:'밸류에이션',decimals:2},
    {code:'KOSPI_PBR',title:'KOSPI PBR',frequency:'D',unit:'배',category:'밸류에이션',decimals:2},
    {code:'WTI',title:'WTI 유가',frequency:'D',unit:'USD',category:'시장가격',decimals:2},
    {code:'USDKRW',title:'원/달러 환율',frequency:'D',unit:'원',category:'시장가격',decimals:1},
    {code:'RRP',title:'미 연준 역레포 잔고',frequency:'D',unit:'십억달러',category:'유동성',decimals:2},
    {code:'TGA',title:'미 재무부 TGA 잔고',frequency:'W',unit:'백만달러',category:'유동성',decimals:0},
    {code:'REDBOOK',title:'Redbook Index',frequency:'W',unit:'% YoY',category:'고빈도 경기',decimals:1,pending:'무료 장기 이력은 없어 최근 공개값부터 주간으로 자체 적재합니다.'},
    {code:'WEI',title:'미국 주간 경제 지수',frequency:'W',unit:'%',category:'고빈도 경기',decimals:2},
    {code:'EMRATIO',title:'미국 인구대비 고용률',frequency:'M',unit:'%',category:'고빈도 경기',decimals:1},
    {code:'US_RETAIL_SALES',title:'미국 소매판매 YoY',frequency:'M',unit:'% YoY',category:'고빈도 경기',decimals:2},
    {code:'KR_EXPORT_DAILY_AVG',title:'한국 일평균 수출',frequency:'T',unit:'억달러/조업일',category:'고빈도 경기',decimals:2,pending:'관세청 1~10일·1~20일·월말 누계와 조업일수로 독립 구간 일평균을 적재합니다.'},
    {code:'US_CPI',compareCode:'US_CORE_CPI',title:'미국 CPI',compareTitle:'Core CPI',frequency:'M',unit:'% YoY',category:'물가',decimals:2},
    {code:'US_PPI',compareCode:'US_CORE_PPI',title:'미국 PPI',compareTitle:'Core PPI',frequency:'M',unit:'% YoY',category:'물가',decimals:2},
    {code:'US_PCE',compareCode:'US_CORE_PCE',title:'미국 PCE 가격지수',compareTitle:'Core PCE 가격지수',frequency:'M',unit:'% YoY',category:'물가',decimals:2},
    {code:'KR_CPI',compareCode:'KR_CORE_CPI',title:'한국 CPI',compareTitle:'Core CPI (식료품·에너지 제외)',frequency:'M',unit:'% YoY',category:'물가',decimals:2},
    {code:'KR_PPI',compareCode:'KR_IMPORT_PRICE',title:'한국 PPI',compareTitle:'수입물가',frequency:'M',unit:'% YoY',category:'물가',decimals:2},
    {code:'US_SBDI_31_180',title:'미국 연체율',frequency:'M',unit:'%',category:BUSINESS_DISTRESS_CATEGORY,decimals:2},
    {code:'DRALACBS',title:'미국 은행 전체대출 연체율',frequency:'Q',unit:'%',category:BUSINESS_DISTRESS_CATEGORY,decimals:2},
    {code:'US_SBDFI',title:'미국 채무불이행률',frequency:'M',unit:'%',category:BUSINESS_DISTRESS_CATEGORY,decimals:2},
    {code:'US_COMMERCIAL_CH11',title:'미국 기업 회생 신청건수',frequency:'M',unit:'건',category:BUSINESS_DISTRESS_CATEGORY,decimals:0},
    {code:'KR_CORP_DELINQ',title:'한국 기업대출 연체율',frequency:'M',unit:'%',category:BUSINESS_DISTRESS_CATEGORY,decimals:2},
    {code:'KR_DEFAULT_COMPANIES',title:'한국 부도업체수',frequency:'M',unit:'개',category:BUSINESS_DISTRESS_CATEGORY,decimals:0},
    {code:'KR_CORP_REHAB',title:'한국 법인회생 신청건수',frequency:'M',unit:'건',category:BUSINESS_DISTRESS_CATEGORY,decimals:0}
  ];
  const freeze = item => Object.freeze({...item});
  const primary = Object.freeze(SERIES.map(freeze));
  const all = Object.freeze(SERIES.flatMap(item => {
    const base = {code:item.code,title:item.legendTitle||item.title,frequency:item.frequency,frequencyLabel:item.frequencyLabel||FREQUENCY_LABELS[item.frequency]||item.frequency,unit:item.unit,category:item.category,decimals:item.decimals,marketScope:marketScope(item.code)};
    return item.compareCode ? [freeze(base),freeze({...base,code:item.compareCode,title:item.compareTitle,marketScope:marketScope(item.compareCode)})] : [freeze(base)];
  }));
  window.MacroWatchEconomicSeriesRegistry=Object.freeze({series:primary,allSeries:all,marketScopes:Object.freeze(['KR','US','GLOBAL']),frequencyLabels:Object.freeze({...FREQUENCY_LABELS}),defaultCategoryOrder:Object.freeze([...DEFAULT_CATEGORY_ORDER]),categories:Object.freeze({rate:RATE_CATEGORY,financialCredit:FINANCIAL_CREDIT_CATEGORY,businessDistress:BUSINESS_DISTRESS_CATEGORY,legacyRate:LEGACY_RATE_CATEGORY,legacyBusinessCredit:LEGACY_BUSINESS_CREDIT_CATEGORY})});
  window.MacroWatchEconomicSeriesCatalog=Object.freeze(primary.map(item=>freeze({code:item.code,title:item.title,frequency:item.frequencyLabel||FREQUENCY_LABELS[item.frequency]||item.frequency,category:item.category})));
})();
