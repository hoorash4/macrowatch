# MacroWatch

경제 지표와 시장 흐름을 확인하고 지표를 추적하는 정적 웹 앱입니다.

## Structure

- `index.html` / `admin.html`: 사용자·관리자 진입 화면
- `assets/js/` / `assets/css/`: 책임별 브라우저 코드와 스타일
- `backend/`: GitHub Actions에서 실행하는 수집·백업 작업
- `supabase/functions/`: 인증, 검색, 단일 확인, 관리자 Edge Functions
- `supabase/migrations/`: 데이터베이스 스키마 변경 이력
- `tools/build/`: 배포 결과물 생성 도구
- `tests/`: 동작·파일 경로·배포 계약 검증
- `docs/`: 명세서, 구조, 인수인계와 작업 기록

## Documentation

- [코드 구조](docs/CODE_STRUCTURE.md)
- [인수인계](docs/HANDOFF.md)
- [유동성 명세](docs/LIQUIDITY_SPEC.md)
- [보안 정책](docs/SECURITY.md)
- [리팩터링 검증·원복 기록](docs/REFACTOR_20260908.md)

루트에는 저장소 안내인 이 파일과 프로젝트 전체 작업 지침인 `AGENTS.md`만 둡니다.

## Local preview

정적 파일이므로 프로젝트 루트에서 HTTP 서버로 열면 됩니다.

```powershell
python -m http.server 8787
```

그 다음 `http://127.0.0.1:8787/`로 접속합니다.

## Deployment

GitHub `main` 변경은 Pages Actions에서 빌드·검증한 뒤 배포됩니다.
이전 JS·CSS 주소는 `tools/build/pages-compat.cjs`가 `_site`에만 생성합니다.
실제 소스는 `assets`에 하나만 있으며, 브라우저의 추가 요청이나 리다이렉트는 없습니다.
브라우저 캐시를 갱신해야 할 때는 변경한 정적 파일의 쿼리 버전을 올립니다.
