-- 파이낸셜뉴스 RSS 수집 타입과 DB 저장 허용값을 일치시킨다.
-- 기존 프로젝트에는 이미 생성된 CHECK 제약조건이 있으므로 명시적으로 교체한다.
alter table public.news_article_sentiments
  drop constraint if exists news_article_sentiments_source_name_check;

alter table public.news_article_sentiments
  add constraint news_article_sentiments_source_name_check
  check (source_name in ('yonhap', 'maekyung', 'financial_news'));
