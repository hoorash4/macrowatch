# MacroWatch 프로젝트 컨텍스트

업데이트 기준: 2026-09-14 KST

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

자동수집, 수동 재계산, 과거 백필, 복구, 유지보수, 헬스체크, 배포는 서로 다른 책임으로 분리한다. 예약 수집이 과거 데이터 재작성이나 무관한 유지보수를 부수효과로 수행하면 안 된다.

## 3. 현재 코드 구조

### 브라우저

- `assets/js/core`: 설정, 공통 API, 인증, 입력·용어 규칙
- `assets/js/dashboard`: 메인 대시보드 탐색·상태·조회·렌더링
- `assets/js/charts`: 공통 차트 유틸과 기능별 차트
- `assets/js/admin`: 관리자 화면과 운영 제어
- `assets/js/policy`: FOMC/정책 브리핑
- `assets/css`: 실제 스타일 소스
- 루트에 JS/CSS 실소스를 중복 저장하지 않는다. 이전 공개 URL 호환은 빌드 결과에서만 생성한다.

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

새 수집기는 동일한 관측값이면 기존 `series_code`를 재사용한다. 새 raw/source/cache/observation 테이블은 명시적인 구조적 예외가 있어야 한다.

## 5. 자동수집과 백필 경계

자동수집은 자신의 현재 발표·수집 구간만 처리한다.

- 확정된 과거 행을 예약 수집이 다시 쓰지 않는다.
- lookback은 현재값 계산을 위한 조회 범위일 뿐 과거 재작성 권한이 아니다.
- 백필·복구·대량 재계산은 명시적 수동 작업이다.
- backend-only 변경이 Pages 배포를 유발하지 않도록 한다.
- Supabase 변경은 새 migration과 실제 영향받는 Edge Function만 배포하는 것이 원칙이다.

## 6. 주요 기능 영역

현재 프로젝트는 다음 기능군을 포함한다.

- 미국·한국·이머징 시장 스트레스 및 신용위험
- 유동성, 정책 기대, 주식/채권 상대가치 및 투자매력
- 미국·한국 기업실적 집계
- 한국 외국인 수급 및 섹터 흐름
- 중소기업 위험 등 거시 보조지표
- 중앙은행 정책/FOMC 분석과 브리핑
- 뉴스 수집·분류·중복 사건 식별·결정적 뉴스 감지
- 경제지표 차트와 사용자 추적/알림
- 관리자 운영 제어, 백업, 상태 점검

세부 산식·정책은 각 구현과 기능별 문서를 기준으로 하며, 이 문서만 보고 임의로 통일하거나 재설계하지 않는다.

## 7. 최근 상태

- 2026-09-08 구조 및 내부 리팩터링이 두 단계로 완료되었고, UI·수치·API/DB 계약·스케줄을 보존하는 검증 기록은 각각 `REFACTOR_20260908.md`, `INTERNAL_REFACTOR_20260908.md`에 남아 있다.
- 2026-09-13 운영 DB의 중복 원천 저장소 정리와 canonical source 전환이 수행되었고, 상세 결과는 `database-data-inventory.md`에 기록되어 있다.
- 최근 main 변경은 뉴스의 결정적 사건 식별·중복 제거 규칙 보강이다.
- 한국 신용스프레드 과거 백필은 완료 후 전용 백필 변경이 제거된 상태다.

현재 상태를 판단할 때는 항상 `main`의 최신 커밋과 실제 GitHub Actions/Supabase 상태를 다시 확인한다. 이 문서의 날짜나 과거 실행 번호를 현재 상태로 간주하지 않는다.

## 8. 테스트와 검증

- Python/Node 테스트로 계산·상태·브라우저 계약을 검증한다.
- Edge Function 변경은 Deno 타입 검사와 관련 계약 테스트를 확인한다.
- Pages 변경은 실제 빌드 산출물과 기존 공개 URL 호환을 검증한다.
- DB·자동수집·배포를 변경했으면 코드 테스트만으로 완료 처리하지 않고 실제 운영 경계까지 확인한다.
- 유료 AI 호출, 실제 알림 발송, 대규모 백필은 단순 회귀 테스트 목적으로 실행하지 않는다.

## 9. 이 문서의 갱신 규칙

다음 변경이 발생하면 같은 작업에서 `PROJECT_CONTEXT.md`도 갱신한다.

- 기능 추가·삭제 또는 주요 동작 변경
- 디렉터리/모듈 책임 구조 변경
- DB canonical source 또는 핵심 데이터 흐름 변경
- Edge Function/API 경계 변경
- 자동수집·백필·배포 정책 변경
- 장기적으로 유지할 중요한 설계 결정

단순 문구 수정, 사소한 버그 수정, 값 조정처럼 프로젝트 이해에 영향을 주지 않는 변경은 기록하지 않는다.

이 문서는 전체 구현의 복사본이 아니라 현재 맥락의 인덱스다. 세부 내용은 연결된 코드·계약·검증 문서를 참조한다.
