create table if not exists public.market_index_prices (
  index_code text not null check (index_code in ('KOSPI')),
  market_date date not null,
  open numeric(12, 4) not null,
  high numeric(12, 4) not null,
  low numeric(12, 4) not null,
  close numeric(12, 4) not null,
  volume numeric(20, 0),
  source text not null default 'data_go_kr',
  updated_at timestamptz not null default now(),
  primary key (index_code, market_date),
  check (high >= low),
  check (high >= open and high >= close),
  check (low <= open and low <= close)
);

alter table public.market_index_prices enable row level security;

comment on table public.market_index_prices is 'KOSPI daily OHLCV used solely to calculate market context for news analysis.';
