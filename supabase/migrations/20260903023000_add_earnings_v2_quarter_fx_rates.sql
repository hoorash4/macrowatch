-- One exchange-rate snapshot belongs to a finalized quarterly universe.
-- Financial collection only reads this snapshot and never calls ECOS inline.

create table if not exists earnings_v2.quarter_fx_rates (
  fiscal_year integer not null,
  fiscal_quarter smallint not null,
  base_currency text not null,
  quote_currency text not null,
  target_date date not null,
  observed_on date not null,
  rate numeric not null,
  source text not null default 'ecos',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (fiscal_year, fiscal_quarter, base_currency, quote_currency),
  constraint quarter_fx_rates_quarter_check check (fiscal_quarter between 1 and 4),
  constraint quarter_fx_rates_base_currency_check check (base_currency ~ '^[A-Z]{3}$'),
  constraint quarter_fx_rates_quote_currency_check check (quote_currency ~ '^[A-Z]{3}$'),
  constraint quarter_fx_rates_pair_check check (base_currency <> quote_currency),
  constraint quarter_fx_rates_rate_check check (rate > 0),
  constraint quarter_fx_rates_observation_check check (observed_on <= target_date)
);

alter table earnings_v2.quarter_fx_rates enable row level security;
revoke all on table earnings_v2.quarter_fx_rates from public, anon, authenticated;
grant select, insert, update on table earnings_v2.quarter_fx_rates to service_role;

create or replace function public.earnings_v2_get_quarter_fx_rate(
  p_fiscal_year integer,
  p_fiscal_quarter integer,
  p_base_currency text,
  p_quote_currency text
)
returns table (
  fiscal_year integer,
  fiscal_quarter smallint,
  base_currency text,
  quote_currency text,
  target_date date,
  observed_on date,
  rate numeric,
  source text
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select
    snapshot.fiscal_year,
    snapshot.fiscal_quarter,
    snapshot.base_currency,
    snapshot.quote_currency,
    snapshot.target_date,
    snapshot.observed_on,
    snapshot.rate,
    snapshot.source
  from earnings_v2.quarter_fx_rates snapshot
  where snapshot.fiscal_year = p_fiscal_year
    and snapshot.fiscal_quarter = p_fiscal_quarter
    and snapshot.base_currency = upper(btrim(p_base_currency))
    and snapshot.quote_currency = upper(btrim(p_quote_currency));
$$;

create or replace function public.earnings_v2_upsert_quarter_fx_rate(p_row jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare
  v_count integer;
begin
  insert into earnings_v2.quarter_fx_rates as target (
    fiscal_year, fiscal_quarter, base_currency, quote_currency,
    target_date, observed_on, rate, source
  )
  select
    incoming.fiscal_year,
    incoming.fiscal_quarter::smallint,
    upper(btrim(incoming.base_currency)),
    upper(btrim(incoming.quote_currency)),
    incoming.target_date,
    incoming.observed_on,
    incoming.rate,
    coalesce(nullif(btrim(incoming.source), ''), 'ecos')
  from jsonb_to_record(p_row) as incoming(
    fiscal_year integer,
    fiscal_quarter integer,
    base_currency text,
    quote_currency text,
    target_date date,
    observed_on date,
    rate numeric,
    source text
  )
  on conflict (fiscal_year, fiscal_quarter, base_currency, quote_currency)
  do update set
    target_date = excluded.target_date,
    observed_on = excluded.observed_on,
    rate = excluded.rate,
    source = excluded.source,
    updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.earnings_v2_get_quarter_fx_rate(integer, integer, text, text)
  from public, anon, authenticated;
revoke all on function public.earnings_v2_upsert_quarter_fx_rate(jsonb)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_get_quarter_fx_rate(integer, integer, text, text)
  to service_role;
grant execute on function public.earnings_v2_upsert_quarter_fx_rate(jsonb)
  to service_role;

comment on table earnings_v2.quarter_fx_rates is
  'Quarter-universe exchange-rate snapshots; financial collection never fetches rates inline.';
