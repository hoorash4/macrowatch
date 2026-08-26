create table if not exists public.market_sector_etf_prices (
  etf_id uuid not null references public.market_sector_etfs(id) on delete cascade,
  market_date date not null,
  open_price numeric(18, 4) not null check (open_price > 0),
  close_price numeric(18, 4) check (close_price > 0),
  volume numeric(24, 0) check (volume >= 0),
  updated_at timestamptz not null default now(),
  primary key (etf_id, market_date)
);

create table if not exists public.market_sector_weekly_rankings (
  week_start date not null,
  etf_id uuid not null references public.market_sector_etfs(id) on delete cascade,
  rank smallint not null check (rank > 0),
  previous_rank smallint check (previous_rank > 0),
  is_new boolean not null default false,
  top10_streak smallint not null default 0 check (top10_streak >= 0),
  weekly_return_pct numeric(12, 6) not null,
  cumulative_return_pct numeric(12, 6) not null,
  price_stage text not null check (price_stage in ('open', 'close')),
  calculated_at timestamptz not null default now(),
  primary key (week_start, etf_id)
);

create index if not exists market_sector_prices_date_idx
  on public.market_sector_etf_prices (market_date desc);
create index if not exists market_sector_rankings_week_rank_idx
  on public.market_sector_weekly_rankings (week_start desc, rank asc);

alter table public.market_sector_etf_prices enable row level security;
alter table public.market_sector_weekly_rankings enable row level security;

drop policy if exists "Authenticated users can read sector ETF registry" on public.market_sector_etfs;
create policy "Authenticated users can read sector ETF registry"
  on public.market_sector_etfs for select to authenticated using (true);

drop policy if exists "Authenticated users can read sector ETF prices" on public.market_sector_etf_prices;
create policy "Authenticated users can read sector ETF prices"
  on public.market_sector_etf_prices for select to authenticated using (true);

drop policy if exists "Authenticated users can read sector rankings" on public.market_sector_weekly_rankings;
create policy "Authenticated users can read sector rankings"
  on public.market_sector_weekly_rankings for select to authenticated using (true);

comment on table public.market_sector_etf_prices is
  'Regular-market ETF opening and closing prices collected from KIS Open API.';
comment on table public.market_sector_weekly_rankings is
  'Recomputed sector ranks; current-week rows are provisional at the opening-price stage.';
