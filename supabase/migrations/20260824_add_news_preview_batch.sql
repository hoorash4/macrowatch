-- Optional marker for temporary dashboard preview aggregates.
-- Production pipeline rows leave this null; a preview batch can be removed in one query.
alter table public.news_daily_article_sentiment
  add column if not exists preview_batch text;

create index if not exists news_daily_article_sentiment_preview_batch_idx
  on public.news_daily_article_sentiment (preview_batch)
  where preview_batch is not null;
