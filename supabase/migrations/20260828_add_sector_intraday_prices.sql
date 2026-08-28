alter table public.market_sector_etf_prices
  add column if not exists latest_price numeric(18, 4),
  add column if not exists price_stage text;

update public.market_sector_etf_prices
set latest_price = coalesce(close_price, open_price),
    price_stage = case when close_price is null then 'open' else 'close' end
where latest_price is null or price_stage is null;

alter table public.market_sector_etf_prices
  alter column latest_price set not null,
  alter column price_stage set not null;

alter table public.market_sector_etf_prices
  drop constraint if exists market_sector_etf_prices_latest_price_check,
  drop constraint if exists market_sector_etf_prices_price_stage_check;

alter table public.market_sector_etf_prices
  add constraint market_sector_etf_prices_latest_price_check check (latest_price > 0),
  add constraint market_sector_etf_prices_price_stage_check check (price_stage in ('open', 'intraday', 'close'));

alter table public.market_sector_weekly_rankings
  drop constraint if exists market_sector_weekly_rankings_price_stage_check;

alter table public.market_sector_weekly_rankings
  add constraint market_sector_weekly_rankings_price_stage_check check (price_stage in ('open', 'intraday', 'close'));

comment on table public.market_sector_etf_prices is
  'ETF opening, latest provisional, and confirmed closing prices collected from KIS Open API.';
comment on table public.market_sector_weekly_rankings is
  'Full sector ranks retained for six weeks; current-week rows can use opening or intraday provisional prices.';
