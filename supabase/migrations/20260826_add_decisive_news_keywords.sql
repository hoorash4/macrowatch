-- Store AI-derived tags only; no source article title, body, or URL is retained.
alter table public.news_extreme_matches
  add column if not exists keywords text[] not null default '{}';

alter table public.news_daily_article_sentiment
  add column if not exists decisive_news_keywords text[] not null default '{}';
