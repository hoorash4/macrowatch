-- Administrator-defined semantic criteria for rare, market-regime-changing news.
-- Source article titles, bodies, and links remain outside the database.
create table if not exists public.news_extreme_rules (
  id uuid primary key default gen_random_uuid(),
  signal text not null check (signal in ('critical_negative', 'critical_positive')),
  phrase text not null check (char_length(trim(phrase)) between 2 and 300),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists news_extreme_rules_active_idx
  on public.news_extreme_rules (is_active, created_at);

alter table public.news_extreme_rules enable row level security;

alter table public.news_article_sentiments
  add column if not exists extreme_signal text
  check (extreme_signal in ('critical_negative', 'critical_positive'));

create index if not exists news_article_sentiments_extreme_signal_idx
  on public.news_article_sentiments (article_date desc, extreme_signal)
  where extreme_signal is not null;

alter table public.news_daily_article_sentiment
  add column if not exists critical_negative_count integer not null default 0
  check (critical_negative_count >= 0),
  add column if not exists critical_positive_count integer not null default 0
  check (critical_positive_count >= 0);

create table if not exists public.news_extreme_alerts (
  article_date date primary key,
  critical_negative_count integer not null default 0 check (critical_negative_count >= 0),
  critical_positive_count integer not null default 0 check (critical_positive_count >= 0),
  sent_at timestamptz,
  status text not null check (status in ('sent', 'failed')),
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.news_extreme_alerts enable row level security;

comment on table public.news_extreme_rules is 'Administrator criteria evaluated semantically by AI; not keyword-only matching.';
comment on column public.news_article_sentiments.extreme_signal is 'AI-derived critical negative or critical positive signal. No article text is stored.';
comment on table public.news_extreme_alerts is 'One Kakao self-chat notification per news collection date when an extreme signal exists.';
