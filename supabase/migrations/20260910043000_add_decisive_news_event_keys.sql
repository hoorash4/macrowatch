-- Keep only a short AI-derived event key. Article title, body and URL remain unstored.
alter table public.news_extreme_matches
  add column if not exists event_key text;

create index if not exists news_extreme_matches_week_event_key_idx
  on public.news_extreme_matches (article_date, event_key)
  where event_key is not null;

alter table public.news_daily_article_sentiment
  add column if not exists decisive_news_event_keys text[] not null default '{}',
  add column if not exists decisive_news_legacy_count integer not null default 0
    check (decisive_news_legacy_count >= 0);

comment on column public.news_extreme_matches.event_key is 'Short AI-derived canonical event identifier used only to collapse duplicate decisive-news coverage within a week.';
