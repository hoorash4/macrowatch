alter table public.news_daily_article_sentiment
  add column if not exists excluded_count integer not null default 0 check (excluded_count >= 0);
