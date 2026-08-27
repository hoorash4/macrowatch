-- KIS ETF 구성종목 원문은 형식 검증 없이 보존한다. 화면의 대표 종목은
-- 편입비중 순위와 종목명만 사용하므로 종목코드 등 누락값도 허용한다.
alter table public.market_sector_etf_holdings
  add column if not exists id uuid default gen_random_uuid();

alter table public.market_sector_etf_holdings
  drop constraint if exists market_sector_etf_holdings_pkey,
  drop constraint if exists market_sector_etf_holdings_holding_ticker_check,
  drop constraint if exists market_sector_etf_holdings_weight_pct_check;

alter table public.market_sector_etf_holdings
  alter column id set not null,
  alter column holding_ticker drop not null,
  alter column holding_name drop not null,
  alter column weight_pct drop not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.market_sector_etf_holdings'::regclass
      and contype = 'p'
  ) then
    alter table public.market_sector_etf_holdings
      add constraint market_sector_etf_holdings_pkey primary key (id);
  end if;
end $$;

comment on table public.market_sector_etf_holdings is
  'Top three ETF constituents ordered by KIS portfolio weight; raw code and nullable fields are preserved without validation.';
