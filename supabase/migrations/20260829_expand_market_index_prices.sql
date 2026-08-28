-- 한 기능마다 지수 종가를 중복 저장하지 않고 기존 공용 시계열을 확장한다.
alter table public.market_index_prices
  drop constraint if exists market_index_prices_index_code_check;

alter table public.market_index_prices
  alter column open drop not null,
  alter column high drop not null,
  alter column low drop not null,
  add column if not exists is_quarter_end boolean not null default false;

alter table public.market_index_prices
  add constraint market_index_prices_index_code_check
  check (index_code in ('KOSPI', 'KOSPI200', 'KOSDAQ150', 'NASDAQ100', 'SP500'));

create index if not exists market_index_prices_quarter_end_idx
  on public.market_index_prices (index_code, market_date desc)
  where is_quarter_end;

comment on table public.market_index_prices is
  'Shared market-index price history. Daily KOSPI rows support news context; quarterly benchmark rows support earnings-price divergence.';
comment on column public.market_index_prices.is_quarter_end is
  'True only for the final valid trading observation in a completed calendar quarter.';
