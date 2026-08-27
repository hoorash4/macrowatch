# Earnings Momentum 데이터 계약

이 문서는 `SPEC_Earnings_Momentum_Leaders.md`를 MacroWatch의 Supabase/PostgreSQL 구조와 기존 자동수집 방식에 맞게 구체화한 구현 계약이다. 원본 명세의 분석 목적과 계산 원칙은 유지하고, 저장소·보안·정정공시 처리만 실제 시스템에 맞게 조정한다.

## 1. 데이터 계층

데이터는 다음 세 계층을 섞지 않는다.

1. **원본 응답**: `earnings_source_payloads`
   - OpenDART·SEC·KIS가 반환한 응답을 요청 단위로 보존한다.
   - API 키, Authorization 헤더 등 비밀값은 `request_params`에 저장하지 않는다.
2. **공시 이력과 원계정**: `earnings_filings`, `earnings_financial_facts`
   - 공시 접수번호와 정정 관계를 보존한다.
   - 한 공시 안의 연결·별도 계정과 원본 행을 재현할 수 있게 저장한다.
   - 기존 행을 고쳐 쓰지 않고 정정공시는 새 filing으로 추가한다.
3. **현재 표준 분기값**: `earnings_quarterly_financials`
   - 집계에 사용할 단일 분기 매출·영업이익·순이익·EPS만 저장한다.
   - 정정공시가 오면 이 계층만 새 filing 기준으로 교체한다.
   - YoY, Delta, 순위, Leader Score는 이후 파생계층에서 계산한다.

기업과 지수 소속도 분리한다. 기업은 `earnings_companies`에 한 번만 존재하고, 복수 지수 소속은 `earnings_index_memberships`에 각각 저장한다.

## 2. OpenDART 기본 수집 경로

한국 기업은 다음 순서로 수집한다.

1. `corpCode.xml`로 종목코드와 `corp_code`를 동기화한다.
2. 공시목록 API로 신규 사업·반기·분기보고서와 정정공시를 탐지한다.
3. 주요계정 다중회사 API `fnlttMultiAcnt.json`을 기본 재무 수집 경로로 사용한다.
4. 중복 기업을 제거한 `corp_code`를 최대 100개씩 묶는다.
5. 동일 사업연도·보고서 코드별로 배치 호출한다.
6. 다중회사 응답에서 필수 계정이 누락된 기업만 `fnlttSinglAcntAll.json`으로 보완한다.

OpenDART 보고서 코드는 다음과 같이 해석한다.

| 보고서 코드 | 의미 | 목표 분기 |
| --- | --- | --- |
| `11013` | 1분기보고서 | Q1 |
| `11012` | 반기보고서 | Q2 |
| `11014` | 3분기보고서 | Q3 |
| `11011` | 사업보고서 | Q4 |

다중회사 호출의 `request_key`는 최소한 `사업연도/보고서코드/정렬된 corp_code 배치`를 식별해야 한다. 응답 본문의 SHA-256도 저장해 같은 응답의 중복 적재를 막는다.

## 3. OpenDART 계정 선택

표준 metric은 `revenue`, `operating_income`, `net_income`, `eps` 네 개다.

- 정확한 계정명 문자열 하나에 의존하지 않는다.
- 우선 표준 계정 ID를 사용하고, 허용된 계정명 별칭은 명시적인 매핑 테이블 또는 코드 상수로 관리한다.
- 연결재무제표(`CFS`)를 우선한다.
- 연결 값이 없을 때만 별도재무제표(`OFS`)를 사용한다.
- 한 분기의 metric을 CFS와 OFS에서 임의로 섞지 않는다.
- EPS가 주요계정 API에 없으면 전체 재무제표 조회로 보완하고, 그래도 없으면 `NULL`과 `missing_metrics`를 남긴다.

`earnings_financial_facts`에는 공급자 계정 ID·계정명·원본 필드와 원본 행을 함께 저장한다. 계정 매핑을 나중에 바꾸더라도 API를 다시 호출하지 않고 표준 분기값을 재생성할 수 있어야 한다.

## 4. 단일 분기값 변환

공급자가 제공한 당기 단일기간 값과 누적값을 구분한다.

- Q1: Q1 누적값을 단일 분기값으로 사용한다.
- Q2: 반기보고서에 신뢰 가능한 3개월 당기값이 있으면 그 값을 우선한다. 없으면 H1 누적값에서 Q1 누적값을 뺀다.
- Q3: 3분기보고서에 신뢰 가능한 3개월 당기값이 있으면 그 값을 우선한다. 없으면 9M 누적값에서 H1 누적값을 뺀다.
- Q4: 사업보고서 FY 누적값에서 9M 누적값을 뺀다.

차감에 필요한 앞 분기 누적값이 없거나 회계기간·통화·연결구분이 다르면 계산하지 않는다. 해당 metric은 `NULL`, 품질상태는 `partial` 또는 `review_required`로 저장한다.

## 5. 분기 식별

다음 값을 함께 저장한다.

- `fiscal_year`, `fiscal_quarter`: 기업 회계연도 기준
- `period_start`, `period_end`: 실제 보고기간
- `market_year`, `market_quarter`: 지수 집계 기준

한국 초기 버전은 원칙적으로 회계분기와 시장분기를 동일하게 매핑하되 실제 `period_end`를 반드시 검증한다. 미국 기업은 비달력 회계연도가 흔하므로 SEC 어댑터 구현 전에 별도 시장분기 매핑 규칙을 확정한다.

## 6. 정정공시와 멱등성

정정공시는 원공시를 덮어쓰지 않는다.

1. 새 `earnings_filings` 행을 만들고 가능한 경우 `corrects_filing_id`를 연결한다.
2. 정정공시에서 추출한 facts를 새 filing 아래 저장한다.
3. 유효한 최신 공시를 선택해 `earnings_quarterly_financials`를 교체한다.
4. `canonical_version`을 증가시킨다.
5. 이후 단계에서 해당 분기와 영향을 받는 후속 Delta·지수집계·순위를 재계산한다.

재시도 시에는 다음 키로 중복을 방지한다.

- 원본 응답: source + operation + request_key + payload hash
- 공시: source + source_filing_id
- 원계정: filing_id + source_row_key
- 표준 분기값: company_id + fiscal_year + fiscal_quarter

## 7. 보안과 공개 범위

- GitHub Actions와 백엔드는 `SUPABASE_SERVICE_ROLE_KEY`로 적재한다.
- OpenDART 키는 환경변수에서만 읽고 DB·로그·원본 요청 파라미터에 남기지 않는다.
- 원본 응답, 공시 이력, 원계정 테이블은 클라이언트 읽기 정책을 만들지 않는다.
- 로그인 사용자는 기업·지수·소속·표준 분기값만 읽을 수 있다.
- 관리자 교정 기능이 필요해지면 브라우저에서 원본 테이블을 직접 수정하지 않고 관리자 Edge Function을 거친다.

## 8. 다음 단계 경계

이 기반 단계에서는 아래 작업을 하지 않는다.

- 실제 KOSPI100·KOSDAQ50 구성종목 입력
- OpenDART API 호출
- YoY·Delta·마진 계산
- KIS 가격·시가총액 수집
- Top10·Leader Score 계산
- 프론트엔드 표시

다음 단계는 이 계약을 사용하는 OpenDART 클라이언트와 응답 파서 구현이다.

## 9. 운영 일정과 누락 복구

일일 증분 확인 시간은 다른 MacroWatch 자동작업과 겹치지 않도록 다음과 같이 고정한다.

- 미국 SEC: 한국시간 화~토요일 13:30
- 한국 OpenDART: 한국시간 월~금요일 19:30

고정된 최근 3영업일만 계산하지 않는다. 소스별 마지막 성공 확인 시각을 저장하고, 평상시에도 최소 최근 14일을 겹쳐 조회한다. 수집이 14일 넘게 중단됐다면 마지막 성공 시점보다 이틀 앞에서부터 조회 범위를 자동 확장한다. 원본 응답 hash와 공시 접수번호의 고유키로 재조회된 자료를 제거한다.

이 방식은 설·추석 같은 장기 연휴, 미국과 한국의 서로 다른 휴장일, API 반영 지연, 자동작업 실패 후 재개를 별도 휴일 달력 없이 처리한다. DART의 18:00~19:00 익일 접수분은 다음 영업일 조회에서 반영한다.
