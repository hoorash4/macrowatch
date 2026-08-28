-- Compact quarterly company prices and recalculable earnings/price disparity.
-- Daily OHLCV is intentionally excluded: this feature needs one adjusted
-- closing price per completed calendar quarter only.
create table if not exists public.earnings_company_quarterly_prices (
  company_id uuid not null references public.earnings_companies(id) on delete cascade,
  market_year integer not null check (market_year between 1900 and 2200),
  market_quarter smallint not null check (market_quarter between 1 and 4),
  price_date date not null,
  adjusted_close numeric(24, 8) not null check (adjusted_close > 0),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  source text not null,
  updated_at timestamptz not null default now(),
  primary key (company_id, market_year, market_quarter)
);

create index if not exists earnings_company_quarterly_prices_period_idx
  on public.earnings_company_quarterly_prices (market_year desc, market_quarter desc, company_id);

create table if not exists public.earnings_company_price_gaps (
  company_id uuid not null references public.earnings_companies(id) on delete cascade,
  market_year integer not null check (market_year between 1900 and 2200),
  market_quarter smallint not null check (market_quarter between 1 and 4),
  base_market_year integer not null check (base_market_year between 1900 and 2200),
  base_market_quarter smallint not null check (base_market_quarter between 1 and 4),
  price_date date not null,
  adjusted_close numeric(24, 8) not null check (adjusted_close > 0),
  ttm_operating_income numeric(38, 8),
  normalized_price numeric(24, 8),
  normalized_ttm_operating_income numeric(24, 8),
  gap_pct numeric(24, 8),
  gap_delta_pp numeric(24, 8),
  calculation_state text not null check (calculation_state in (
    'normal', 'missing_ttm', 'nonpositive_ttm', 'missing_base'
  )),
  calculation_version integer not null default 1 check (calculation_version > 0),
  calculated_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (company_id, market_year, market_quarter)
);

create index if not exists earnings_company_price_gaps_period_idx
  on public.earnings_company_price_gaps (market_year desc, market_quarter desc, company_id);

alter table public.earnings_company_quarterly_prices enable row level security;
alter table public.earnings_company_price_gaps enable row level security;

drop policy if exists "Authenticated users can read company quarterly prices"
  on public.earnings_company_quarterly_prices;
create policy "Authenticated users can read company quarterly prices"
  on public.earnings_company_quarterly_prices for select to authenticated using (true);

drop policy if exists "Authenticated users can read company earnings price gaps"
  on public.earnings_company_price_gaps;
create policy "Authenticated users can read company earnings price gaps"
  on public.earnings_company_price_gaps for select to authenticated using (true);

create or replace function public.list_current_earnings_price_companies()
returns table(company_id uuid, country text, ticker text, exchange text, currency text)
language sql
security definer
set search_path = public, pg_temp
as $$
  select distinct companies.id, companies.country, companies.ticker,
         companies.exchange, companies.reporting_currency
  from public.earnings_companies companies
  join public.earnings_index_memberships memberships
    on memberships.company_id = companies.id and memberships.effective_to is null
  where companies.ticker is not null
  order by companies.country, companies.id;
$$;

revoke all on function public.list_current_earnings_price_companies()
  from public, anon, authenticated;
grant execute on function public.list_current_earnings_price_companies()
  to service_role;

comment on table public.earnings_company_quarterly_prices is
  'One corporate-action-adjusted closing price per company and completed calendar quarter.';
comment on table public.earnings_company_price_gaps is
  'Quarterly rebased TTM operating-income and adjusted-price lines, their ratio gap, and quarter-over-quarter gap delta.';
