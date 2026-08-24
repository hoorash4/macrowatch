alter table public.news_article_sentiments
  add column if not exists collected_at timestamptz;

update public.news_article_sentiments
  set collected_at = created_at
  where collected_at is null;

alter table public.news_article_sentiments
  alter column collected_at set not null,
  alter column collected_at set default now();

comment on column public.news_article_sentiments.published_at is 'Original publisher timestamp.';
comment on column public.news_article_sentiments.article_date is 'Collection date used by daily sentiment aggregation.';
comment on column public.news_article_sentiments.collected_at is 'Timestamp when MacroWatch collected and analyzed the article.';
