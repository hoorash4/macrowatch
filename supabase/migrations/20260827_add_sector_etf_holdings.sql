create table if not exists public.market_sector_etf_holdings (
  etf_id uuid not null references public.market_sector_etfs(id) on delete cascade,
  holding_ticker text not null check (holding_ticker ~ '^\d{6}$'),
  holding_name text not null,
  weight_pct numeric(10, 6) not null check (weight_pct > 0),
  weight_rank smallint not null check (weight_rank between 1 and 3),
  updated_at timestamptz not null default now(),
  primary key (etf_id, holding_ticker),
  unique (etf_id, weight_rank)
);

create index if not exists market_sector_holdings_etf_rank_idx
  on public.market_sector_etf_holdings (etf_id, weight_rank);

alter table public.market_sector_etf_holdings enable row level security;

drop policy if exists "Authenticated users can read sector ETF holdings" on public.market_sector_etf_holdings;
create policy "Authenticated users can read sector ETF holdings"
  on public.market_sector_etf_holdings for select to authenticated using (true);

comment on table public.market_sector_etf_holdings is
  'Top three listed-company constituents of each representative ETF, ordered by KIS portfolio weight.';
