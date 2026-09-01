# Earnings V1 retirement manifest

상태: 기록 전용 — 삭제 승인 없음  
기준일: 2026-09-01

이 문서는 V2 검증 후 V1을 한 번에 제거하기 위한 체크리스트다. 현재 단계에서는 아래 객체를 변경하거나 삭제하지 않는다.

## 제거 승인 전 필수 조건

- V2 최신 1년 파일럿 승인
- V2 자동 증분 수집 검증
- V2 시장·기업 화면 전환 완료
- V2와 V1 결과 비교 및 누락 사유 승인
- 사용자로부터 V1 파괴적 제거 별도 승인

## V1 코드 영역

- `backend/earnings/` 전체
- `.github/workflows/earnings-*.yml` 중 이름에 `earnings-v2-`가 없는 워크플로
- `supabase/functions/earnings-*` 중 이름에 `earnings-v2-`가 없는 함수
- 프론트의 `earnings_` V1 조회·렌더링 경로

## V1 DB 영역

정확한 제거 SQL은 전환 직전에 운영 DB 카탈로그를 다시 조회해 생성한다. 현재 저장소에서 확인되는 접두사는 다음과 같다.

- `public.earnings_*` 테이블·뷰
- `public.*earnings*` 함수·트리거
- V1 earnings 테이블을 참조하는 예약 작업과 정책

과거 마이그레이션 파일은 운영 이력이라 삭제하지 않는다. V1 제거는 별도 신규 마이그레이션 한 개로 수행한다.

## 제거 후 검증

- `pg_depend`에 V1 참조가 남지 않음
- `cron.job`에 V1 호출이 남지 않음
- Supabase Edge Function 및 GitHub Actions에 V1 실행이 남지 않음
- 프론트 네트워크 요청에 V1 RPC·테이블 호출이 없음
- RLS·함수 권한·DB advisor 재검사

## V2 독립성 경계

- DB: private `earnings_v2` schema
- Python: `backend/earnings_v2/`
- RPC: `public.earnings_v2_*` service-role-only boundary
- 향후 Edge Function/Workflow: `earnings-v2-*`
- 향후 프론트 조회: `get_earnings_v2_*`

V2는 V1 객체를 외래키, import, 함수 호출로 참조하지 않는다.

