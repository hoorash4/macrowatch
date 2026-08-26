-- Extreme-news rules are independent of the normal index-inclusion decision.
-- Store only hashes and classified signal, never article title/body/URL.
create table if not exists public.news_extreme_matches (
  article_date date not null,
  article_hash text not null,
  extreme_signal text not null check (extreme_signal in ('critical_negative', 'critical_positive')),
  created_at timestamptz not null default now(),
  primary key (article_date, article_hash)
);

create index if not exists news_extreme_matches_date_idx
  on public.news_extreme_matches (article_date, extreme_signal);

alter table public.news_extreme_matches enable row level security;
