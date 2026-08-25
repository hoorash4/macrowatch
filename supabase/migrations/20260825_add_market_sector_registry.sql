-- Registry only: sector return collection is intentionally not coupled here.
-- The admin can add, change, retire, and later attach fallback ETFs without
-- changing the dashboard or the existing news/indicator pipelines.
create table if not exists public.market_sector_etfs (
  id uuid primary key default gen_random_uuid(),
  sector_name text not null check (char_length(trim(sector_name)) between 1 and 80),
  etf_name text not null check (char_length(trim(etf_name)) between 1 and 120),
  etf_ticker text not null check (char_length(trim(etf_ticker)) between 1 and 24),
  issuer text not null check (char_length(trim(issuer)) between 1 and 80),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (etf_ticker)
);

create index if not exists market_sector_etfs_active_name_idx
  on public.market_sector_etfs (is_active desc, sector_name asc);

alter table public.market_sector_etfs enable row level security;

comment on table public.market_sector_etfs is
  'Admin-managed representative domestic ETFs for the future sector-flow feature. No price collection is enabled by this registry alone.';
