# MacroWatch 프로젝트 컨텍스트

업데이트 기준: 2026-09-15 KST

이 문서는 새 채팅이나 새 작업 세션에서 MacroWatch의 현재 구조와 핵심 운영 경계를 빠르게 복원하기 위한 요약 문서다. 세부 구현·복구 기록을 모두 복제하지 않고, 현재 상태를 이해하는 데 필요한 진입점과 변경 규칙만 유지한다.

## 1. 문서 우선순위

1. `AGENTS.md` — 작업 원칙과 승인 범위의 최상위 기준. 이 문서는 AGENTS를 대체하거나 수정하지 않는다.
2. `docs/PROJECT_CONTEXT.md` — 현재 프로젝트 구조·기능·운영 상태를 빠르게 복원하는 요약.
3. `docs/CODE_STRUCTURE.md` — 현재 코드 배치와 책임 경계.
4. 도메인 계약 문서 — `canonical-series-contract.md`, `collector-isolation.md`, `SECURITY.md`, 기능별 SPEC.
5. 리팩터링 기록 — `REFACTOR_20260908.md`, `INTERNAL_REFACTOR_20260908.md`. 과거 변경의 검증·복구 근거이며 현재 구조 설명서로 대신 사용하지 않는다.

## 2. 프로젝트 목적과 런타임

MacroWatch는 Evotive Research의 거시경제·시장 모니터링 대시보드 및 알림 서비스다.

- 프론트엔드: GitHub Pages에서 제공하는 정적 HTML/CSS/JavaScript
- 백엔드 수집·계산: Python 파이프라인 + GitHub Actions
- 데이터베이스·Edge Functions: Supabase
- 자동 실행: GitHub Actions와 명시적으로 범위가 정해진 Supabase cron
- 사용자/관리자 진입점: `index.html`, `admin.html`
- 경제지표 전용 화면: `economic-charts.html`
- 과거 시장 국면 분석 전용 화면: `historical-insight.html`

자동수집, 수동 재계산, 과거 백필, 복구, 유지보수, 헬스체크, 배포는 서로 다른 책임으로 분리한다. 예약 수집이 과거 데이터 재작성이나 무관한 유지보수를 부수효과로 수행하면 안 된다.

## 3. 현재 코드 구조

### 브라우저

- `assets/js/core`: 설정, 공통 API, 인증, 입력·용어 규칙
- `assets/js/dashboard`: 메인 대시보드 탐색·상태·조회·렌더링
- `assets/js/charts`: 공통 차트 유틸과 기능별 차트
- `assets/js/admin`: 관리자 화면과 운영 제어
- `assets/js/policy`: FOMC/정책 브리핑
- `assets/js/historical-insight`: Historical Insight의 데이터 조회·차트·향후 사례/국면 로직을 기능 책임별로 분리하는 영역
- `assets/css`: 실제 스타일 소스
- 루트에 JS/CSS 실소스를 중복 저장하지 않는다. 이전 공개 URL 호환은 빌드 결과에서만 생성한다.

Historical Insight는 처음부터 단계별 구현을 전제로 한다. 화면 골격, 시장지수 조회, 사례 정의, 국면 계산, 마커, 저장, 현재 비교를 한 파일에 섞지 않고 책임별 모듈로 분리한다. 공통 Supabase 연결, 숫자 포맷, 전체 페이지 조회(`queryAll`), 차트 테마·등록/해제는 `frontend-core`를 재사용한다. 경제지표 차트도 동일한 페이지 조회 함수를 사용한다. Historical Insight의 크기 관찰은 Lightweight Charts의 `autoSize`로 처리한다. 사이클 정의의 세부 계약과 현재 확정 기준점은 `HISTORICAL_CYCLE_DEFINITION.md`를 기준으로 한다.

경제지표 metadata는 `economic-series-catalog.js`가 코드·표시명·범주·주기·단위의 단일 기준점이다. Historical Insight의 지표 사용 가능 여부는 canonical `economic_chart_series_points`를 집계한 `economic_chart_series_coverage`의 실제 최초·최종 관측일로 판정한다. 추세 구조는 원래 관측 주기를 보존한 raw 값과 주기별 smoothing으로 판정하고, 차트 오버레이에만 표시 구간별 0~100 정규화를 사용한다. smoothing은 구조 판정에만 쓰며 최종 피봇 날짜와 값은 raw 시계열의 실제 극점 또는 실제 추세 출발점으로 되돌린다. `pivotDate`, `regimeBoundaryDate`, `breakoutOrBreakdownDate`, `confirmationDate`, `confirmationEvidence`는 별도 필드다. 방향 추세에서 끝나는 전환은 실제 극점을 보존한다. sideways에서 방향 추세로 나가는 전환은 breakout/breakdown으로 이어진 raw 시계열의 실제 출발 극점을 피봇으로 사용하고, range 이탈일은 확인 근거로 별도 저장한다.

현재국면 분석은 완결된 과거사례의 retrospective 엔진과 물리적으로 분리된 online 상태 엔진으로 실행한다. 기준점이 없는 최신 구간은 `watch / candidate / structural_only`로 관리하며, `structural_only`는 지표 자체의 구조 전환이 확인됐지만 시장 관련 피봇으로 확정되지 않았다는 뜻이다. 현재 사례에 START/PEAK/TROUGH가 입력된 뒤에만 각 기준점의 -3개월~+1개월 창을 적용하고, 넓은 전후 경로에서 구조적으로 유효한 피봇만 `market_relevant_confirmed`로 분류한다. 과거 사례에서 START/PEAK/TROUGH 중 한 번이라도 의미 있는 피봇이 있었던 지표 전체를 상시 목록으로 유지한다. 조정·반등 watch에서는 최근 구조 신호를 별도로 유지하고, 기존 추세 방향의 새 extreme이나 range 복귀가 나오면 후보를 무효화하거나 새 극점으로 교체한다. 유효 후보와 structural-only 신호는 각자 후보일 기준 ±1개월 안의 다른 유효 신호로 별도 시너지 그룹을 만들며, 후보가 무효화되면 자기 점수와 모든 상대 후보의 시너지 보너스를 즉시 다시 계산한다. 주가 피봇 가능성은 구조 품질, 지속성, 시너지, 시장 기준점 관련 확정, 무효화 회수를 지표별 한 번만 반영한 결정론적 집계이며 통계적 예측 확률이 아니다. 현재 시장 기준점과 국면명은 관리자가 입력·수정·삭제할 수 있고, 저장 직후 관련성 분류와 점수 전체를 다시 계산한다. 현재 진행 중인 사례가 없으면 이름은 `historical_current_settings`의 기본값인 `현재 국면 관찰 중`을 사용한다.

### Python

- `backend/common.py`: 환경변수, FRED, Supabase, 알림 공통 처리
- `backend/sources`: 여러 파이프라인이 공유하는 원천 조회 어댑터
- `backend/signals`: 거시·시장 지표 수집, 계산, 저장 진입점
- `backend/tracking`: 사용자 지표 추적·조건 판정·알림
- `backend/operations`: 백업과 예약 알림 등 운영 작업
- `backend/earnings_common`: 공통 실적 모델·변환·통신 기반
- `backend/earnings_v2`: 한국 기업실적 자동수집
- `backend/earnings_v25`: 금융회사 공급자·전송 정책
- `backend/earnings_us`: 미국 구성종목·실적 파이프라인

서로 다른 파이프라인을 다른 파이프라인의 소스 라이브러리처럼 사용하지 않는다. 공통 원천 조회는 `sources`, 공통 실적 기반은 `earnings_common`에 둔다.

### Supabase

- 외부 API 진입점은 `supabase/functions/<function>/index.ts`
- `_shared/policy`, `_shared/market`, `_shared/news`는 도메인 공통 코드
- 주요 기능은 관리자 제어, 지표 검색/알림, 뉴스 분석, 정책 분석, 시장 컨텍스트, 외국인 수급, 섹터 흐름 등으로 분리되어 있다.
- migration은 운영 이력이며 죽은 코드처럼 임의 삭제하지 않는다.

## 4. 데이터의 canonical 원칙

재사용 가능한 경제·시장 시계열의 읽기 계약은 `public.economic_chart_series_points`다.

- 외부 원천 관측값: `economic_chart_points`
- 재사용 가능한 계산·리샘플 시계열: `economic_chart_derived_points`
- Cleveland Fed 물가 nowcast vintage: `inflation_nowcast_vintages`
- OHLC, ETF 메타데이터처럼 단일 시계열로 손실 없이 표현할 수 없는 데이터는 해당 구조화 테이블을 유지한다.
- 합성지수, 모델 최종값, 뉴스 분석, 사용자 설정, 작업 상태는 목적별 결과 테이블에 둔다.
- 기능별 파생 테이블에 재사용 가능한 원천값 복사본을 만들지 않는다.

시장지수 일봉 OHLC의 canonical 저장소는 `market_index_prices`다. Historical Insight는 로그인 사용자의 SELECT 정책으로 기본 3개 지수만 읽으며 브라우저 쓰기 권한은 부여하지 않는다.

현재 기본 지수는 다음 3개다.

- `SP500` — S&P 500
- `NASDAQ_COMPOSITE` — Nasdaq Composite
- `KOSPI` — KOSPI

이 3개 지수는 1990년부터의 과거 분석과 Historical Insight의 기본 시장 원천으로 사용한다. 수익률, 이동평균, 이격도, drawdown, 변동성, rebasing, 비율·상대강도 등 원시 지수에서 계산 가능한 값은 별도 외부 수집 없이 내부 파생값으로 계산한다.

새 수집기는 동일한 관측값이면 기존 `series_code` 또는 canonical 테이블을 재사용한다. 새 raw/source/cache/observation 테이블은 명시적인 구조적 예외가 있어야 한다.

## 5. 시장지수 수집 정책

`backend/signals/market_index_collection.py`가 S&P 500, Nasdaq Composite, KOSPI 일봉 수집의 기준 경로다.

백필과 자동수집은 의도적으로 분리한다.

- 백필: 장기 이력 확보를 위해 Yahoo 일봉 사용을 허용한다.
- 미국 자동수집: Yahoo를 당일 잠정 OHLC로 사용하고, 이후 한 번의 미국 거래 세션이 지난 뒤 FRED 종가로 검증·승격한다.
- KOSPI 자동수집: KRX 직접 수집을 canonical 경로로 사용한다.
- 최근 구간은 잠정값 승격과 정정 반영을 위해 자동수집 시 제한된 lookback 범위 내에서만 upsert한다.

Historical Insight와 기타 downstream 계산은 동일한 `market_index_prices` 원시행을 기준으로 사용하고, 동일 지수를 별도 경로에서 중복 수집하지 않는다.

## 6. 자동수집과 백필 경계

자동수집은 자신의 현재 발표·수집 구간만 처리한다.

- 확정된 과거 행을 예약 수집이 임의로 전체 재작성하지 않는다.
- lookback은 최신 잠정값 검증·정정과 현재값 계산을 위한 제한된 조회 범위다.
- 백필·복구·대량 재계산은 명시적 수동 작업이다.
- backend-only 변경이 Pages 배포를 유발하지 않도록 한다.
- Supabase 변경은 새 migration과 실제 영향받는 Edge Function만 배포하는 것이 원칙이다.

## 7. 주요 기능 영역

현재 프로젝트는 다음 기능군을 포함한다.

- 미국·한국·이머징 시장 스트레스 및 신용위험
- 유동성, 정책 기대, 주식/채권 상대가치 및 투자매력
- 미국·한국 기업실적 집계
- 한국 외국인 수급 및 섹터 흐름
- 중소기업 위험 등 거시 보조지표
- 중앙은행 정책/FOMC 분석과 브리핑
- 뉴스 수집·분류·중복 사건 식별·결정적 뉴스 감지
- 경제지표 차트와 사용자 추적/알림
- Historical Insight: 과거 시장 국면을 이용해 현재 시장을 해석하는 분석 기능
- 관리자 운영 제어, 백업, 상태 점검

### Historical Insight 최종 방향

Historical Insight는 단순 과거 차트 조회가 아니라 다음 흐름을 목표로 한다.

1. 주요 역사적 시장 사례 정의
2. 각 사례의 관찰 구간 표시
3. `start / peak / trough` 기준점 확정
4. 기준점·구간별 주요 거시·시장 팩트 추출
5. 상승률·하락률·drawdown·금리·스프레드 등 파생값 계산
6. 현재 시장 상태와 과거 사례 비교
7. 유사 과거 국면과 전환 신호 표시

초기 사례는 닷컴버블, 카드대란, 중국 산업재 버블, 글로벌 금융위기, 2009~2011 유동성장, 2012~2014 미국 유동성장, 메모리 슈퍼사이클, 코로나 충격, 2022 금리인상/긴축장, AI/반도체 상승장 등 10개다. 한국 IT버블은 닷컴버블의 KOSPI 보조 비교로 다룬다. 사례와 기간은 화면에 하드코딩하지 않고 DB 정의에서 읽는다.

현재 구현 원칙은 한 번에 전체 기능을 완성하지 않고 단계별로 진행하는 것이다. 각 단계는 구현 → 테스트 → 실제 화면/데이터 검증 후 다음 단계로 넘어간다.

현재 단계 순서는 다음과 같다.

- Phase 1: 실제 지수 데이터 렌더링 및 지수 전환
- Phase 2~6: 10개 사례의 관찰 구간, START/PEAK/TROUGH 마커, canonical 종가 기반 파생값, 관리자 편집, Historical Case 영속 저장 구조
- Phase 7: 시장별 START/PEAK/TROUGH 기준점 이전에 공개 가능했던 canonical 최신 관측값을 결정론적으로 연결하는 팩트 추출
- Phase 8: 구간 파생값
- Phase 9: 현재 vs 과거 유사도 비교
- Phase 10: 사례 탐색·현재 비교·팩트 시트·전환 신호 통합

세부 산식·정책은 각 구현과 기능별 문서를 기준으로 하며, 이 문서만 보고 임의로 통일하거나 재설계하지 않는다.

## 8. 최근 상태

- 2026-09-08 구조 및 내부 리팩터링이 두 단계로 완료되었고, UI·수치·API/DB 계약·스케줄을 보존하는 검증 기록은 각각 `REFACTOR_20260908.md`, `INTERNAL_REFACTOR_20260908.md`에 남아 있다.
- 2026-09-13 운영 DB의 중복 원천 저장소 정리와 canonical source 전환이 수행되었고, 상세 결과는 `database-data-inventory.md`에 기록되어 있다.
- 2026-09-15 `Historical Insight` 별도 페이지와 리서치 툴 내비게이션 구조가 추가되었다.
- 2026-09-15 S&P 500, Nasdaq Composite, KOSPI의 1990년 이후 일봉 백필을 `market_index_prices`에 구성했고, Historical Insight는 이 canonical 지수를 사용한다.
- Historical Insight Phase 1은 원천 조회·차트·화면 상태를 분리하여 실제 지수 렌더링, 지수 전환, 전체 기간 복귀, 로딩/빈 데이터/오류 재시도를 구현했다.
- 10개 Historical Case의 설명·관찰 범위는 `historical_cases`, 사례 × 시장별 확정 날짜는 `historical_case_market_cycles`에 저장한다. 지수 탭을 전환하면 해당 시장의 START/PEAK/TROUGH와 파생 성과가 함께 전환된다. 종가·상승률·하락률·drawdown·기간은 canonical 지수에서 계산하며, 진행 중인 AI/반도체 상승장의 시장별 미확정 peak/trough는 null로 유지한다. 한국 IT버블은 닷컴버블의 KOSPI 비교로 포함하고 별도 사례로 만들지 않는다.
- Phase 7 기준점 팩트는 `historical_case_anchor_facts` 보안 호출자 뷰에서 원천값을 복제하지 않고 `economic_chart_series_points`에 연결한다. 일·주·월 지표별 공개 지연과 최대 허용 이력을 적용해 기준일 이후 정보가 섞이지 않게 하며, 미확정 기준점에는 팩트를 만들지 않는다. 기준점 팩트는 내부 후속 분석용이며 현재 프론트엔드에는 직접 표시하지 않는다.
- Historical Insight 화면은 `과거사례 분석`과 `현재국면 분석` 탭으로 분리하되 동일한 화면 골격을 공유한다. 진행 중인 사례는 과거 목록에서 제외하고 현재국면 왼쪽 패널에는 현재국면 제목과 차트 추가 지표 목록을 둔다. 각 시장의 TROUGH 저장 시 해당 사이클을 확정하며, 모든 시장 사이클이 확정된 사례는 과거사례 목록으로 이동한다.
- 비교 지표 목록은 라디오 버튼으로 한 번에 하나만 차트에 표시한다. 선택 지표는 보라·마젠타 계열의 단일 강조색을 사용한다. 과거사례의 분석 단위는 `시장 기준점 × 지표`다. 시장별 START/PEAK/TROUGH를 먼저 확정하고, 시장 추세 길이로 정한 1·2·3개월 최소기간을 전체 retrospective segmentation과 최종 validation에 동일하게 적용해 실제 구조 피봇을 먼저 확정한다. 순차 segmentation이 임시 sideways/pending 전환이나 regime reset 때문에 명확한 최고점·최저점을 놓쳐도, 원시계열의 전체 전후 경로를 별도로 검사해 상승→지속 하락과 하락→지속 상승의 실제 extreme을 독립 검증한다. 그 피봇 날짜가 기준점의 -3개월~+1개월 안에 있을 때만 market-relevant pivot으로 연결하며 window 내부의 국소 극점이나 임시 경계를 대체 피봇으로 선택하지 않는다. 기준점별 유효 조합을 모두 계산한 뒤 전역 assignment에서는 배정 수와 구조 전환의 확실성만 최적화한다. 구조 전환의 규모·명확성과 이후 regime 지속성으로 계산한 structural score를 그대로 `pivotSelectionScore`로 고정해 피봇 선택의 기준으로 사용하며, 시장 추세 대비 duration·선행/후행 timing·relationship·최종 base score는 피봇 선택에 사용하지 않는다. 동일 `pivotDate` 또는 동일 structural boundary는 START/PEAK/TROUGH 중 하나에만 귀속한다. 같은 지표가 세 기준점 모두에서 의미 있으면 세 결과를 보존하되 각 결과는 서로 다른 structural pivot이어야 한다. retrospective 경로는 직전 추세 진행폭, 되돌림 깊이, 관측 주기별 변동성, 경로 효율, 기존 방향 복귀와 새 극점 갱신을 함께 사용해 correction/rebound를 통합한다. 최소기간은 독립 regime의 잡음 배제 하한이며 실제 피봇 날짜를 확정일까지 미루는 기준이 아니다. 구조가 없거나 최소 유효기간을 충족하지 않으면 기준점 결과는 null이다. relationship은 `nextRegime` 하나가 아니라 START=상승 시작, PEAK=하락 시작, TROUGH=하락 종료라는 시장 transition role과 지표의 `previousRegime→nextRegime` 역할을 비교해 1차 판정한다. 피봇 고유 배정이 끝난 뒤 positive·inverse 가설은 선택된 피봇들의 `pivotSelectionScore`를 사이클 전체에서 합산하고, 한쪽이 유효 관계 증거의 60% 이상을 설명할 때 cycle-level relationship으로 채택한다. 반대 구조 증거는 `conflict`, 불충분한 증거는 `unresolved_evidence`로 기록하며 해당 기준점의 relationship bonus만 0으로 만들고 structural pivot과 base score는 유지한다. 어느 가설도 우세하지 않으면 사이클 관계는 `unresolved`다. Pearson·lag correlation·부호 안정성은 confidence와 bonus의 보조 증거로만 사용하며 피봇 선택이나 관계 방향 결정에는 사용하지 않는다. 선택된 피봇의 최종 평가에만 선행/후행 timing을 사용하여 structural validity·timing·duration의 최종 base score에 반영하고, 허용된 relationship bonus를 더한다. 현재국면은 별도 online 경로에서 과거 어느 시장에서든 의미 있었던 전체 지표를 감시한다. 시간 경과만으로 추세를 종료하거나 후보·확정으로 승격하지 않으며, 직전 추세 진행폭 대비 되돌림 깊이, 지표 변동성, 전고점·전저점 갱신, 횡보 범위의 구조적 이탈과 복귀 여부를 함께 판정한다. sideways 이후 새 방향 후보는 raw 시계열의 실제 출발 극점을 유지하고 breakout/breakdown 날짜는 확인 근거로만 기록한다. 최소 regime 기간은 구조적 전환이 확인된 뒤의 잡음 배제 하한으로만 사용하고, 얕은 correction/rebound는 기간과 관계없이 watch로 유지한다. 새 극점 갱신이나 기존 범위 복귀는 후보를 무효화하고 기여도와 시너지 보너스를 회수한다. 시장 기준점이 없으면 watch/candidate/structural_only로 유지하고, 관리자가 입력한 기준점과 구조적으로 연결된 경우에만 market_relevant_confirmed로 승격한다.

현재 상태를 판단할 때는 항상 `main`의 최신 커밋과 실제 GitHub Actions/Supabase 상태를 다시 확인한다. 이 문서의 날짜나 과거 실행 번호를 현재 상태로 간주하지 않는다.

## 9. 테스트와 검증

- Python/Node 테스트로 계산·상태·브라우저 계약을 검증한다.
- Edge Function 변경은 Deno 타입 검사와 관련 계약 테스트를 확인한다.
- Pages 변경은 실제 빌드 산출물과 기존 공개 URL 호환을 검증한다.
- DB·자동수집·배포를 변경했으면 코드 테스트만으로 완료 처리하지 않고 실제 운영 경계까지 확인한다.
- 유료 AI 호출, 실제 알림 발송, 대규모 백필은 단순 회귀 테스트 목적으로 실행하지 않는다.

## 10. 이 문서의 갱신 규칙

다음 변경이 발생하면 같은 작업에서 `PROJECT_CONTEXT.md`도 갱신한다.

- 기능 추가·삭제 또는 주요 동작 변경
- 디렉터리/모듈 책임 구조 변경
- DB canonical source 또는 핵심 데이터 흐름 변경
- Edge Function/API 경계 변경
- 자동수집·백필·배포 정책 변경
- 장기적으로 유지할 중요한 설계 결정

단순 문구 수정, 사소한 버그 수정, 값 조정처럼 프로젝트 이해에 영향을 주지 않는 변경은 기록하지 않는다.

이 문서는 전체 구현의 복사본이 아니라 현재 맥락의 인덱스다. 세부 내용은 연결된 코드·계약·검증 문서를 참조한다.
