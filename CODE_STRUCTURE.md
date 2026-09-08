# MacroWatch 코드 구조

## 보존 경계

HTML 진입점은 `index.html`과 `admin.html`이다. 함수 URL, DB 계약, 예약 실행,
계산 정책은 내부 배치와 별개로 유지한다. 백필과 자동 정책은 합치지 않는다.

## 브라우저 책임

| 위치 | 책임 |
| --- | --- |
| `assets/js/core` | 설정, 공통 API 클라이언트, 인증, 입력·용어 규칙 |
| `assets/js/dashboard/script.js` | 화면 탐색, 로더 등록, 지표 검색·추적·드래그·CRUD |
| `assets/js/dashboard/dashboard-charts.js` | 뉴스·스트레스·신용위험·섹터 조회와 렌더링 |
| `assets/js/charts/analysis-chart-utils.js` | 보간, 축 범위, 스크롤 공통 처리 |
| `assets/js/charts/*-chart.js` | 기능별 차트 조회와 렌더링 |
| `assets/js/admin` | 관리자 화면, 정책 검토, 카드 순서 |
| `assets/js/policy` | FOMC 브리핑 |
| `assets/css/styles.css` | 기존 규칙 순서를 유지한 스타일 |

전역 공개 API와 HTML 로딩 순서는 유지한다. 공통 기반을 사용하더라도 각
그래프의 표현과 산식을 임의로 통일하지 않는다.

## Python 책임

| 위치 | 책임 |
| --- | --- |
| `backend/earnings_common` | 실적 통신·재시도·실행 제한 공통 기반 |
| `backend/earnings_v2` | 한국 v2 백필과 별도 자동 수집 정책 |
| `backend/earnings_v25` | 과거 한국 백필 정책과 금융위 상세 진단 |
| `backend/earnings_us` | 미국 구성종목·실적 정책 |
| `backend/sources/market.py` | 월간 예측·주식투자 매력이 공유하는 Yahoo/FRED 관측값 처리 |
| `backend/common.py` | 기존 환경변수·FRED·Supabase·알림 공통 처리 |
| `backend/*_pipeline.py` | 지표별 수집 순서·산식·저장·실행 진입점 |

파이프라인이 다른 파이프라인을 소스 라이브러리로 사용하지 않도록 공유 조회를
소스 모듈에 둔다. `earnings_v25/transport_policy.py`는 기존 상세 오류를 유지한다.
v2와 v2.5의 합산·대체 규칙은 동일하지 않아 공통화하지 않았다.

## Supabase와 운영

`supabase/functions/<기존 함수명>/index.ts`는 외부 API 진입점이다.
`_shared`를 사용하는 기존 연결을 유지한다. DB migration은 이력과 운영 계약이므로
죽은 코드처럼 삭제하지 않는다. `.github/workflows`는 GitHub가 요구하는 위치다.

## 검증과 진행 기록

- Python·Node 테스트는 계산, 상태, 화면 계약을 확인한다.
- `tests/test_asset_paths.js`: HTML·CSS의 파일 참조.
- `tests/test_earnings_transport.py`: 요청 횟수·대기·파이프라인별 진단 차이.
- `tests/run_checks.ps1`: 로컬 기본 검사. 모든 화면·운영 검증을 대체하지 않는다.
- `docs/REFACTOR_20260908.md`: 원복 기준, 단계별 변경·검증, 남은 작업.

전체 리팩터링은 진행 중이다. 이 문서는 현재 배치와 책임을 설명하며 완료 선언이 아니다.
