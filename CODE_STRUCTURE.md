# MacroWatch 코드 구조

이 문서는 기능을 수정할 때 어디를 먼저 확인해야 하는지 설명한다. 화면 디자인과
지수 산식은 각각 한 곳에서만 결정하고, 다른 구역은 그 결과를 사용하기만 한다.

## 브라우저 JavaScript

`script.js`는 사이트 규모에 맞춰 한 파일을 유지하지만 즉시 실행 함수 안에
캡슐화되어 있다. 외부에는 다음 두 종류만 공개한다.

- `window.MacroWatchDashboard.loadAll()`: 로그인 완료 후 모든 대시보드 데이터를 읽는다.
- HTML 버튼이 직접 호출하는 등록·수정·삭제 핸들러

파일 내부 순서는 다음과 같다.

1. Supabase 설정과 `DashboardApi`
2. 화면 상태
3. 뉴스 흐름 분석
4. 미국 시장 스트레스
5. 이머징 시장 스트레스
6. 한국 시장 스트레스
7. 미국 신용위험 구성지표
8. 화면 탐색과 초기화
9. 지표 검색
10. 지표 추적 목록·탭·드래그·CRUD
11. 외부 공개 인터페이스

서버 함수를 새로 호출할 때는 직접 `fetch`를 복제하지 말고 `DashboardApi.invoke()`를
사용한다. 차트 산식은 렌더링 함수 안에서만 다루고 DB 저장 로직을 넣지 않는다.

`auth.js`와 `admin.js`는 각각 로그인과 관리자 화면만 담당하며, 자체 즉시 실행
함수로 전역 상태를 차단한다.

## Python 작업

`backend/common.py`는 모든 예약 작업이 공유하는 기반 모듈이다.

- 필수 환경변수 읽기
- FRED observations 요청
- 미발표 기간의 최근값 이월
- 상한 없는 고정 기준 점수 계산
- Supabase REST 요청과 upsert
- 카카오 토큰 갱신과 텍스트 발송

각 실행 파일은 고유한 원천 파싱과 산식, 실행 순서만 담당한다.

- `financial_stress_pipeline.py`: 미국 신용·시장 스트레스
- `korea_stress_pipeline.py`: 한국 시장 스트레스와 비교 지표
- `em_stress_pipeline.py`: 이머징 시장 스트레스와 EEM 비교선
- `check_targets.py`: 사용자 추적 지표 수집과 조건 판정
- `send_news_extreme_alert.py`: 결정적 뉴스 일일 알림
- `upload_drive_backup.py`: 암호화된 DB 백업 교체

지수 가중치와 고정 기준 범위는 각 지수 파일 상단에서만 변경한다. 공통 모듈은
지수 구성을 알지 못하게 유지한다.

## Supabase Edge Functions

`supabase/functions/_shared`에는 뉴스 AI, 시장 맥락, 지표 계산처럼 여러 함수가
공유하는 코드가 있다. 각 `index.ts`는 인증, 입력 검증, 공유 모듈 호출, 응답 조립만
담당하는 것을 원칙으로 한다.

뉴스 RSS는 피드별로 독립 수집한다. 일부 실패 시 성공 데이터는 처리하지만 실패
목록을 API 응답과 GitHub Actions 경고에 남긴다. 부분 누락을 성공으로 숨기지 않는다.

## 변경 전후 검증

로컬에서는 다음 명령 하나로 문법과 주요 계산 계약을 확인한다.

```powershell
./tests/run_checks.ps1
```

GitHub에서는 `Verify code structure` 워크플로가 같은 검사를 자동 실행한다. 지수 산식,
표시 날짜, 알림 조건 또는 HTML escaping을 바꿀 때는 먼저 해당 테스트의 기대 결과가
사용자 요구와 함께 변경됐는지 확인한다.

DB 테이블·열·RLS 정책은 이 문서의 코드 리팩터링 범위에 포함하지 않는다. 스키마는
별도 점검 단계에서 실제 읽기·쓰기 경로와 함께 검토한다.
