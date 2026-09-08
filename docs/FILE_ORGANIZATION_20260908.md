# 파일 정리 후속 작업

## 변경 범위

- 일반 루트 문서 4개를 docs로 이동하고 README의 안내 경로를 갱신했다.
- AGENTS.md는 전체 프로젝트 지침 적용을 위해, README.md는 저장소 안내를 위해 루트에 유지한다.
- 루트 JS 19개와 CSS 1개의 호환 템플릿은 제거했다. 실소스는 assets에 유지한다.
- 동일한 이전 공개 주소는 빌드 결과물에만 생성한다. dashboard의 구버전 캐시용 의존 포함과 CSS 이미지 경로도 유지한다.
- Pages 소스 설정은 branch 빌드에서 Actions 빌드로 전환한다. CI와 실제 배포는 같은 reusable workflow로 결과물을 검증한다.
- DB, 서버 함수, 수집 스케줄, 브라우저 코드와 디자인은 변경하지 않는다.

## 원복

이 변경 전 main은 b5ebf33424101fea9f5aff785284111ef26d8675다.
이 후속 변경을 revert한 경우 GitHub Pages 소스도 원래의 main/root branch 방식으로 되돌린다.
저장된 DB를 복구하거나 재수집할 필요는 없다.

## 검증

루트 배치 검사와 실제 Jekyll 결과물 검사로 새 경로 및 기존 URL의 내용 일치를 확인한다.
PR #35로 병합했고 Pages 배포 34182083963이 성공했다.
Python 343개, Node 61개 및 전체 Edge 타입 검사가 통과했다.
공개 배포의 기존 JS 주소 19개 모두 실제 소스와 내용이 일치하며,
CSS·이전/새 문서 주소·사용자/관리자 페이지가 HTTP 200으로 열림을 확인했다.
GitHub main 루트 파일은 .gitignore, AGENTS.md, README.md, index.html, admin.html만 남았다.
Pages 소스 설정만 MCP의 지원 범위 밖이어서 브라우저로 전환했으며,
나머지 GitHub 상태와 결과는 MCP로 확인했다.
