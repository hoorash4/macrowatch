-- Article text is analyzed in memory only and is never saved to this database.
create table if not exists public.news_article_sentiments (
  id uuid primary key default gen_random_uuid(), article_hash text not null unique,
  source_name text not null check (source_name in ('yonhap', 'maekyung')),
  published_at timestamptz not null, article_date date not null,
  ai_sentiment text not null check (ai_sentiment in ('positive', 'negative', 'neutral', 'uncertain')),
  derived_keywords text[] not null default '{}', uncertain_summary text,
  admin_sentiment text check (admin_sentiment in ('positive', 'negative', 'neutral')),
  admin_resolved_at timestamptz, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  check ((ai_sentiment = 'uncertain') or (cardinality(derived_keywords) = 0 and uncertain_summary is null)),
  check ((admin_sentiment is null) or ai_sentiment = 'uncertain')
);
create index if not exists news_article_sentiments_unresolved_idx on public.news_article_sentiments (published_at desc) where ai_sentiment = 'uncertain' and admin_sentiment is null;
create index if not exists news_article_sentiments_date_idx on public.news_article_sentiments (article_date desc);
create table if not exists public.news_daily_article_sentiment (
  article_date date primary key, positive_count integer not null default 0 check (positive_count >= 0), negative_count integer not null default 0 check (negative_count >= 0), neutral_count integer not null default 0 check (neutral_count >= 0), uncertain_count integer not null default 0 check (uncertain_count >= 0), analyzed_article_count integer not null default 0 check (analyzed_article_count >= 0), generated_at timestamptz not null default now(), check (analyzed_article_count = positive_count + negative_count + neutral_count + uncertain_count)
);
alter table public.news_article_sentiments enable row level security;
alter table public.news_daily_article_sentiment enable row level security;
drop policy if exists "Authenticated users can read article sentiment totals" on public.news_daily_article_sentiment;
create policy "Authenticated users can read article sentiment totals" on public.news_daily_article_sentiment for select to authenticated using (true);
comment on table public.news_article_sentiments is 'Article-level derived classifications only. No article title, body or URL is stored.';
comment on table public.news_daily_article_sentiment is 'Daily article classification totals for the stacked sentiment chart.';
