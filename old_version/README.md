# MacroWatch

경제 지표를 추적하고 카카오톡 알림을 보내는 정적 웹 앱입니다.

## Structure

- `index.html` / `styles.css`: 사용자 화면과 공통 스타일
- `config.js`: 브라우저용 Supabase 공개 설정의 단일 소스
- `auth.js`: 카카오 인증과 세션 상태 관리
- `script.js`: 지표 등록·조회·정렬 UI
- `admin.html` / `admin.js`: 관리자 화면
- `backend/`: GitHub Actions에서 실행하는 수집·백업 작업
- `supabase/functions/`: 인증, 검색, 단일 확인, 관리자 Edge Functions
- `supabase/migrations/`: 데이터베이스 스키마 변경 이력

## Local preview

정적 파일이므로 프로젝트 루트에서 HTTP 서버로 열면 됩니다.

```powershell
python -m http.server 8787
```

그 다음 `http://127.0.0.1:8787/`로 접속합니다.

## Deployment

GitHub `main` 브랜치가 GitHub Pages에 배포됩니다. 브라우저 캐시를 갱신해야 할 때는 변경한 정적 파일의 쿼리 버전을 올립니다.
