# MacroWatch 데이터베이스 전수조사 및 canonical source 결정

조사 기준은 2026-09-13 운영 Supabase 프로젝트 `xhghpywvthjuvespzdul`의 `public` 스키마이다. 관리형 `auth`, `storage` 스키마는 애플리케이션 데이터 중복 조사 대상에서 제외했다. 조사 당시 public 테이블은 48개, 인덱스를 포함한 총 크기는 약 63.3MB였다. 아래 행 수는 조사 시점 실측 또는 PostgreSQL 통계값이며 운영 수집에 따라 변한다.

## 저장 원칙

- 재사용 가능한 외부 원천 경제·시장 관측값은 `economic_chart_points(series_code, observation_date)`에 저장한다.
- 재사용 가능한 계산·리샘플 시계열은 `economic_chart_derived_points(series_code, observation_date)`에 분리하며, 소비자는 `economic_chart_series_points` 읽기 뷰를 사용한다.
- OHLC, ETF 메타데이터처럼 단일 숫자 시계열 구조로 손실 없이 표현할 수 없는 데이터는 해당 공용 구조화 테이블을 canonical source로 유지한다.
- 합성지수, 모델 최종값, 뉴스 분석, 사용자 설정과 작업 상태는 목적별 결과 테이블에 둔다.
- 파생 테이블에는 원천값 복사본을 두지 않는다. 계산에 사용한 원천의 기준일처럼 결과의 의미를 설명하는 provenance는 유지한다.
- 백필은 별도 수동 경로이며 자동 수집은 최근 발표분만 확인한다.

## 적용 결과

2026-09-13 canonical 이관과 소비자 코드 전환을 먼저 배포한 뒤, 운영 조회·저장 RPC를 검증하고 중복 저장소를 제거했다. public 테이블은 48개에서 42개로, 인덱스를 포함한 총 크기는 약 63.3MB에서 32MB로 줄었다. `economic_chart_points`는 114,550행을 유지했고 `(series_code, observation_date)` 중복은 0건이었다. 유동성, 미국·한국·이머징 스트레스, 외국인 수급 등 주요 파생 결과 테이블의 행 수와 최신 기준일도 제거 전후 동일했다.

제거 대상으로 판정한 구형 저장소의 런타임 참조는 회귀 테스트로 차단했다. canonical 원자료와 파생 결과를 함께 저장하는 RPC는 롤백 트랜잭션으로 실행 검증했으며 `SECURITY INVOKER`와 `service_role` 전용 실행 권한을 유지한다.

## 전수조사 결과

| 테이블 | 행 수 / 크기 | 기간·주기 | 의미·원천 | 수집·사용처 | 분류·결정 |
|---|---:|---|---|---|---|
| `economic_chart_points` | 114,550 / 23MB | 1990-01-01~2026-09-11, D/W/M/Q/T/E | FRED, 미 재무부, ECOS, KOSIS, BLS, BEA, KIS, Yahoo 등의 재사용 시계열 | 경제차트, 알림, 각 지수 collector와 대시보드 | **공용 canonical 원천 저장소**. `(series_code, observation_date)`가 식별자 |
| `economic_chart_derived_points` | 배포 후 운영 집계 | D/W/M/Q/T/E | canonical 원천을 계산·리샘플한 재사용 시계열 | 경제차트, 알림, 각 지수와 대시보드 | **공용 파생 저장소**. 외부 제공자 출처를 직접 저장할 수 없음 |
| `inflation_nowcast_vintages` | 배포 후 운영 집계 | 발표 vintage | Cleveland Fed가 발표한 CPI/PCE headline·core nowcast | 통합물가 계산 | 목표월과 관측일이 모두 필요한 전용 원천 구조 |
| `automatic_source_points` | 109,990 / 28MB | 1970-01-01~2026-09-12, 혼합 | 인플레이션 자체모델용 장기 원자재·시장자료와 기능별 캐시 | 구 인플레이션 ridge, EM capacity, 주식·채권 모델 | 가장 큰 사일로. 필요한 비인플레이션 원천은 canonical로 이관하고 테이블 삭제 |
| `liquidity_observations` | 28,060 / 2.8MB | 2016-08-01~2026-09-12, D/W/M | FRED·BOK·ECOS 유동성 원천 | `liquidity_pipeline.py` | canonical로 이관 후 삭제 |
| `liquidity_indices` | 4,077 / 1.64MB | 2021-09-01~2026-09-04, W/M | 공용 원천에서 계산한 미국·한국 환경/모멘텀/압력/여력 점수 | 유동성 카드 | 파생 결과 유지. 미사용 원천·구성점수 JSON 제거 |
| `policy_expectation_spreads` | 6,676 / 1.34MB | 2000-01-03~2026-09-10, D | 미 3개월·2년 국채와 EFFR로 계산한 기대 스프레드 | 정책기대 collector·차트 | 스프레드 3종만 유지. US3M·US2Y·EFFR 원천 열은 canonical로 이관 후 제거 |
| `equity_bond_source_monthly` | 1,178 / 368kB | 2002-01-01~2026-08-01, M | SPY, TLT, T10Y2Y, BAA10Y 월말 원천 복사 | 주식·채권 상대가치 모델 | canonical로 이관 후 삭제 |
| `us_treasury_10y_daily` | 1,675 / 216kB | 2020-01-02~2026-09-11, D | 미 재무부 10년물 | 물가 카드의 임시 fallback | `US10Y`와 중복. 이관 검증 후 삭제 |
| `korea_foreign_flow_raw` | 1,964 / 256kB | 2018-09-10~2026-09-11, D | KIS 외국인 순매수·거래대금, ECOS 원/달러 | 외국인 수급 Edge Function | 세 원천을 canonical로 이관 후 삭제 |
| `korea_foreign_flow_daily` | 1,224 / 352kB | 2021-09-09~2026-09-11, D | 수급비율·원화변화·인과 z-score·최종 수급지수 | 외국인 수급 카드, 한국 유동성 | 파생 결과 유지. 세 원천 복사 열 제거 |
| `us_market_stress_index_monthly` | 38 / 72kB | 2023-08-01~2026-09-01, M | EBP·CMDI 합성 MSI | 미국 스트레스 카드 | 최종 지수 유지. S&P 500 비교 원천 열 제거 |
| `us_market_tension_weekly` | 160 / 96kB | 2023-08-25~2026-09-11, W | HY OAS·NFCI·자금스프레드·레버리지 합성 | 미국 스트레스 보조지표 | 지수·모멘텀 유지, 모든 원천 열 제거 |
| `em_market_stress_weekly` | 160 / 96kB | 2023-08-25~2026-09-11, W | EM OAS·달러·테일위험 합성 | 이머징 스트레스 카드 | 최종 지수 유지. 4주 평균과 EEM 원천 복사 열 제거 |
| `korea_market_stress_monthly` | 37 / 80kB | 2023-09-01~2026-09-01, M | BOK FSI 70% + 시장구성 30% | 한국 스트레스 카드 | 최종 지수·시장구성지수 유지, 원천 열 제거 |
| `korea_market_stress_weekly` | 193 / 96kB | 2023-01-06~2026-09-11, W | KOSPI·회사채·단기자금 비교 원천 | 한국 스트레스 비교선 | canonical 이관 후 테이블 삭제 |
| `em_capital_capacity_daily` | 700 / 208kB | 2023-11-21~2026-09-10, D | 달러·실질금리·HY OAS·NFCI 합성 | 이머징 자금여건 카드 | 최종 capacity만 유지, 4개 원천 열 제거 |
| `us_small_business_risk_monthly` | 120 / 88kB | 2016-09-01~2026-08-01, M | NFIB 차입난·매출전망, Equifax 연체율 합성 | 미국 중소기업 위험 카드 | 최종 지수와 연체율 기준월 유지. NFIB·연체율 값은 canonical |
| `kr_small_business_risk_monthly` | 81 / 80kB | 2020-01-01~2026-09-01, M | KOSIS/KBIZ 자금전망·가동률, ECOS 연체율 합성 | 한국 중소기업 위험 카드 | 최종 지수와 각 원천 기준월 유지. 원천 값은 canonical |
| `us_inflation_monthly` | 79 / 96kB | 2020-01-01~2026-08-01, M | 공식 CPI/PCE/PPI 합성 및 잠정 물가·실질금리 | 통합물가 카드 | 모델 최종 결과 유지. 잠정치는 Cleveland Fed Nowcast 기반 |
| `equity_bond_relative_forecasts` | 154 / 168kB | 2013-11-01~2026-08-01, M | 공용 시장 원천의 walk-forward 예측 | 주식·채권 상대가치 | 모델 결과·검증 메타데이터 유지 |
| `equity_bond_attractiveness_weekly` | 2,331 / 992kB | 2019-02-22~2026-09-11, W | 실적·주가·금리 기반 국가별 매력 점수 | 주식투자 매력 카드 | 계산비용 있는 파생 결과 유지 |
| `korea_export_intramonth_snapshots` | 68 / 104kB | 2017-03-01~2026-09-01, 10일/20일/월말 | 관세청 누적 수출 스냅샷 | 월중 일평균 수출 계산 | 누적 스냅샷이라는 독립 원천 구조라 유지 |
| `market_index_prices` | 262 / 128kB | 2016-03-31~2026-09-11, D/Q | KOSPI OHLCV와 실적 비교용 지수 가격 | 뉴스 시장맥락, 실적 | OHLCV 공용 canonical 구조로 유지 |
| `market_sector_etfs` | 34 / 120kB | 현재 레지스트리 | 관리자 관리 ETF 메타데이터 | 섹터 수집 | 기능 상태·메타데이터 유지 |
| `market_sector_etf_prices` | 1,632 / 776kB | 2026-07-06~2026-09-11, D | KIS ETF OHLC/확정 상태 | 섹터 순위 | ETF별 구조화 가격의 canonical 원천, 유지 |
| `market_sector_etf_holdings` | 102 / 112kB | 최신 스냅샷 | KIS ETF 상위 구성종목 | 섹터 카드 | 기능 원천 스냅샷 유지 |
| `market_sector_weekly_rankings` | 201 / 184kB | 2026-08-03~2026-09-07, W | ETF 가격으로 계산한 순위 | 주도섹터 카드 | 파생 결과 유지 |
| `central_bank_policy_events` | 228 / 496kB | 2000-02-02~2026-07-29, 회의별 | FOMC·한은 회의와 정책 분석 결과 | 통화정책 시그널, 기준금리 canonical 생성 | 독립 사건·모델 결과 유지 |
| `central_bank_policy_analysis_history` | 0 / 24kB | 회의별 | 재분석 전 정책 결과 스냅샷 | 정책 파이프라인 | 감사 이력 구조 유지 |
| `policy_briefing_alerts` | 0 / 16kB | 사건별 | FOMC 브리핑 발송 outbox | 알림 파이프라인 | 기능 상태 유지 |
| `news_article_sentiments` | 1,708 / 848kB | 2026-08-23~2026-09-13, 기사별 | 기사 분류 결과 | 뉴스 흐름 | 파생 분석 유지 |
| `news_daily_article_sentiment` | 21 / 80kB | 2026-08-23~2026-09-13, D | 기사 분류 일별 집계 | 뉴스 막대 차트 | 반복 집계 결과 유지 |
| `news_events` | 65 / 168kB | 2026-08-18~2026-08-24, 사건별 | 중복 기사를 묶은 사건 결과 | 결정적 뉴스 | 파생 사건 유지 |
| `news_event_sources` | 62 / 104kB | 사건별 | 사건과 원 기사 연결 | 뉴스 추적 | 관계 데이터 유지 |
| `news_daily_sentiment` | 3 / 32kB | 2026-08-18~2026-08-24, D | 사건 기준 일별 심리 | 뉴스 흐름 | 파생 집계 유지 |
| `news_event_feedback` | 0 / 16kB | 사건별 | 사용자 피드백 | 뉴스 개선 | 기능 상태 유지 |
| `news_extreme_rules` | 9 / 48kB | 규칙별 | 관리자 극단 뉴스 기준 | 뉴스 파이프라인 | 설정 유지 |
| `news_extreme_matches` | 103 / 128kB | 2026-08-28~2026-09-13 | 규칙-사건 판정 결과 | 극단 알림 | 파생 관계 유지 |
| `news_extreme_alerts` | 9 / 32kB | 2026-08-28~2026-09-08 | 일별 극단 알림 outbox | 알림 | 기능 상태 유지 |
| `news_pipeline_runs` | 20 / 56kB | 2026-08-25~2026-09-13, 실행별 | 뉴스 수집 완료·중복 방지 상태 | 뉴스 스케줄 | 운영 상태 유지 |
| `targets` | 14 / 120kB | 사용자별 | 지표 추적 조건 | 추적알림·경제차트 | 사용자 기능 데이터 유지 |
| `alert_events` | 16 / 64kB | 이벤트별 | 조건 충족 기록 | 추적알림 | 기능 결과 유지 |
| `notification_channels` | 2 / 48kB | 사용자별 | 카카오·이메일 설정 | 개인설정·알림 | 사용자 설정 유지 |
| `device_tokens` | 0 / 24kB | 기기별 | 푸시 토큰 | 알림 | 기능 상태 유지 |
| `user_accounts` | 2 / 112kB | 사용자별 | 계정·권한 | 인증·관리자 | 사용자 데이터 유지 |
| `app_settings` | 3 / 32kB | 키별 | 전역 애플리케이션 설정 | 프론트·관리자 | 설정 유지 |
| `api_sources` | 0 / 24kB | 소스별 | API 소스 레지스트리 | 현재 실사용 없음 | 비어 있으나 향후 관리자 소스 기능과 연결돼 있어 이번 범위에서는 보존 |
| `economic_chart_preferences` | 1 / 32kB | 사용자별 | 차트 순서·숨김·수평선 | 경제차트 | 사용자 설정 유지 |
| `economic_chart_catalog_settings` | 1 / 32kB | 전역 | 카테고리 순서 | 경제차트 관리자 | 설정 유지 |

## canonical series 이관표

| 제거되는 저장소/열 | canonical series | 주요 소비자 |
|---|---|---|
| `us_treasury_10y_daily` | `US10Y` | 경제차트, 통합물가 |
| `policy_expectation_spreads.treasury_3m_rate/treasury_2y_rate/effr_rate` | `US3M`, `US2Y`, `EFFR` | 정책기대, 경제차트 |
| `liquidity_observations` | `US_SOFR`, `US_IORB`, `US_IOER`, `RRP`, `US10Y_REAL`, `NFCI_CREDIT`, `US_FED_ASSETS`, `TGA`, `KR_CALL_RATE`, `KR_M2`, `KR_LF`, `KR_KOFR`, `KR_BOP_*` | 미국·한국 유동성 |
| `korea_foreign_flow_raw` | `KR_FOREIGN_NET_BUY`, `KOSPI_TRADING_VALUE`, `USDKRW` | 외국인 수급 |
| 미국 스트레스 원천 열 | `US_EBP`, `US_CMDI`, `HY_OAS_WEEKLY`, `NFCI_CREDIT`, `NFCI_RISK`, `NFCI_NONFIN_LEVERAGE`, `US_SHORT_FUNDING_SPREAD`, `SP500_*` | 미국 MSI |
| 한국 스트레스 원천 열/주간 테이블 | `KR_BBB_YIELD`, `KR_AA_YIELD`, `KR3Y`, `KR_CP91`, `KR_CD91`, `KR_KORIBOR3M`, `KR_KOFR`, `BOK_FSI`, `KOSPI_*` | 한국 MSI |
| 이머징 스트레스/여력 원천 열 | `EM_HY_OAS`, `EM_DOLLAR_INDEX`, `EM_TAIL_RISK_OAS`, `VXEEM`, `EEM_WEEKLY_CLOSE`, `US10Y_REAL`, `NFCI` | 이머징 MSI·자금여력 |
| 미국 중소기업 원천 열 | `US_NFIB_SALES_EXPECTATION`, `US_NFIB_BORROWING_DIFFICULTY`, `US_NFIB_OPTIMISM`, `US_SBDI_31_180` | 미국 중소기업 위험 |
| 한국 중소기업 원천 열 | `KR_SME_FUNDING_OUTLOOK`, `KR_SME_UTILIZATION_SA`, `KR_SME_LOAN_DELINQ`, `KR_SME_HEADLINE_OUTLOOK` | 한국 중소기업 위험 |
| `equity_bond_source_monthly`·관련 캐시 | `SPY_ADJUSTED_CLOSE`, `TLT_ADJUSTED_CLOSE`, `US10Y_REAL`, `US10Y2Y`, `BAA10Y`, `NFCI` | 주식·채권 상대가치 |

## 인플레이션 단순화

삭제한 자체 모델은 18개 장기 원자재 선물과 달러·금리·주거비 계열을 별도 캐시에 보관하고 ridge 모델을 매번 학습했다. 새 파이프라인은 canonical 공식 YoY 시계열 `US_CPI`, `US_CORE_CPI`, `US_PPI`, `US_CORE_PPI`, `US_PCE`, `US_CORE_PCE`를 사용한다. 확정치는 PCE 60%·CPI 30%·PPI 10%로 계산하고, 미발표 월의 CPI/PCE는 Cleveland Fed가 직접 제공하는 YoY Nowcast를 사용하며 PPI는 최신 공식 값을 사용한다. 원자재 장기 캐시, ridge 모델, 학습 데이터, 모델 백필 경로와 관련 코드는 제거한다.

## 삭제 검증 조건

1. canonical 이관 SQL이 원본 키별 누락 여부를 검사한다.
2. 새 collector와 프론트가 구형 테이블·원천 열을 참조하지 않는지 저장소 전체 검색과 테스트로 확인한다.
3. 새 코드 배포 후 실제 조회와 저장 RPC를 확인한다.
4. 위 조건을 모두 통과한 뒤 `20260913234000_remove_duplicate_source_storage.sql`로 원천 복사 열과 구형 테이블을 제거한다.

## 이번 범위에서 변경하지 않은 조사 결과

- `market_index_prices`와 `market_sector_etf_prices`는 각각 OHLCV 및 ETF 상태를 보존하므로 단일 숫자 `economic_chart_points`와 구조적으로 같지 않다. 각 테이블 자체를 해당 자료형의 공용 canonical source로 유지했다.
- `api_sources`는 현재 0행이고 직접 소비가 확인되지 않았지만 관리자 소스 레지스트리 목적의 스키마이므로 무단 삭제하지 않았다.
- 뉴스·실적·알림 테이블은 일반 경제 원천의 복사본이 아니라 사건, 관계, 사용자 상태 또는 계산 결과다. 이번 중복 시계열 제거 대상이 아니다.
