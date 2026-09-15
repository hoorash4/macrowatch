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
- Phase 7: 팩트 추출
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
