-- Earnings V2 is intentionally isolated from the legacy earnings objects.
-- It stores only durable source facts, final universe membership, UI-ready
-- calculations, and bounded checkpoint state. No V1 object is referenced.

create schema if not exists earnings_v2;
revoke all on schema earnings_v2 from public, anon, authenticated;
grant usage on schema earnings_v2 to service_role;

create table earnings_v2.companies (
  company_id text primary key,
  country text not null check (country in ('KR', 'US')),
  company_name text not null,
  reporting_currency text not null check (
    (country = 'KR' and reporting_currency = 'KRW') or
    (country = 'US' and reporting_currency = 'USD')
  ),
  entity_kind text not null default 'general' check (entity_kind in ('general', 'financial')),
  listed_from date,
  delisted_on date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (delisted_on is null or listed_from is null or delisted_on >= listed_from)
);

create table earnings_v2.company_identifiers (
  company_id text not null references earnings_v2.companies(company_id) on delete cascade,
  identifier_type text not null check (identifier_type in ('dart_corp_code', 'cik', 'ticker', 'isin', 'krx_code')),
  identifier_value text not null,
  exchange text,
  valid_from date not null default date '1900-01-01',
  valid_to date,
  is_primary boolean not null default false,
  created_at timestamptz not null default now(),
  primary key (company_id, identifier_type, identifier_value, valid_from),
  check (valid_to is null or valid_to >= valid_from)
);

create unique index earnings_v2_one_active_primary_identifier
  on earnings_v2.company_identifiers(company_id, identifier_type)
  where is_primary and valid_to is null;
create index earnings_v2_identifier_lookup
  on earnings_v2.company_identifiers(identifier_type, identifier_value, valid_from, valid_to);

create table earnings_v2.company_quarters (
  company_id text not null references earnings_v2.companies(company_id) on delete cascade,
  fiscal_year integer not null check (fiscal_year between 1900 and 2200),
  fiscal_quarter smallint not null check (fiscal_quarter between 1 and 4),
  period_start date,
  period_end date not null,
  market_year integer not null check (market_year between 1900 and 2200),
  market_quarter smallint not null check (market_quarter between 1 and 4),
  top_line numeric(38, 4),
  operating_income numeric(38, 4),
  net_income numeric(38, 4),
  currency text not null,
  consolidation_scope text not null check (consolidation_scope in ('CFS', 'OFS')),
  top_line_method text not null check (top_line_method in ('reported_total', 'financial_income_sum')),
  operating_income_yoy_pct numeric(20, 8),
  operating_income_yoy_state text not null default 'missing_prior',
  net_income_yoy_pct numeric(20, 8),
  net_income_yoy_state text not null default 'missing_prior',
  operating_income_qoq_sa_pct numeric(20, 8),
  operating_income_qoq_state text not null default 'insufficient_history',
  net_income_qoq_sa_pct numeric(20, 8),
  net_income_qoq_state text not null default 'insufficient_history',
  source text not null check (source in ('open_dart', 'sec_edgar')),
  source_filing_id text not null,
  filing_date date not null,
  revision_reference_date date,
  quality_status text not null default 'draft' check (quality_status in ('draft', 'review_required', 'complete')),
  calculation_version integer not null default 1 check (calculation_version > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (company_id, fiscal_year, fiscal_quarter),
  check (period_start is null or period_end >= period_start),
  check (quality_status <> 'complete' or (top_line is not null and operating_income is not null and net_income is not null)),
  check ((operating_income_yoy_state = 'normal') = (operating_income_yoy_pct is not null)),
  check ((net_income_yoy_state = 'normal') = (net_income_yoy_pct is not null)),
  check ((operating_income_qoq_state = 'normal') = (operating_income_qoq_sa_pct is not null)),
  check ((net_income_qoq_state = 'normal') = (net_income_qoq_sa_pct is not null)),
  check (operating_income_yoy_state in ('normal', 'missing_prior', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening', 'loss_unchanged', 'from_zero', 'currency_mismatch', 'scope_mismatch')),
  check (net_income_yoy_state in ('normal', 'missing_prior', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening', 'loss_unchanged', 'from_zero', 'currency_mismatch', 'scope_mismatch')),
  check (operating_income_qoq_state in ('normal', 'missing_prior', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening', 'loss_unchanged', 'from_zero', 'currency_mismatch', 'scope_mismatch', 'insufficient_history')),
  check (net_income_qoq_state in ('normal', 'missing_prior', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening', 'loss_unchanged', 'from_zero', 'currency_mismatch', 'scope_mismatch', 'insufficient_history'))
);

create index earnings_v2_company_quarters_market_period
  on earnings_v2.company_quarters(market_year, market_quarter, company_id);

create table earnings_v2.universe_members (
  market_id text not null check (market_id in ('kr_largecap', 'kr_kosdaq', 'us_largecap', 'us_nasdaq')),
  market_year integer not null check (market_year between 1900 and 2200),
  market_quarter smallint not null check (market_quarter between 1 and 4),
  reference_date date not null,
  company_id text not null references earnings_v2.companies(company_id) on delete restrict,
  market_cap_rank integer not null check (market_cap_rank > 0),
  market_cap numeric(38, 4) not null check (market_cap >= 0),
  currency text not null check (currency in ('KRW', 'USD')),
  selection_method text not null check (selection_method in ('direct_market_cap', 'reconstructed_revenue500', 'new_listing_override')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (market_id, market_year, market_quarter, company_id),
  unique (market_id, market_year, market_quarter, market_cap_rank)
);

create index earnings_v2_universe_company_period
  on earnings_v2.universe_members(company_id, market_year, market_quarter);

create table earnings_v2.market_quarters (
  market_id text not null check (market_id in ('kr_largecap', 'kr_kosdaq', 'us_largecap', 'us_nasdaq')),
  market_year integer not null check (market_year between 1900 and 2200),
  market_quarter smallint not null check (market_quarter between 1 and 4),
  average_operating_income numeric(38, 4),
  average_net_income numeric(38, 4),
  operating_income_yoy_pct numeric(20, 8),
  operating_income_yoy_state text not null default 'missing_prior',
  net_income_yoy_pct numeric(20, 8),
  net_income_yoy_state text not null default 'missing_prior',
  operating_income_qoq_sa_pct numeric(20, 8),
  operating_income_qoq_state text not null default 'insufficient_history',
  net_income_qoq_sa_pct numeric(20, 8),
  net_income_qoq_state text not null default 'insufficient_history',
  actual_company_count integer not null check (actual_company_count >= 0),
  target_company_count integer not null check (target_company_count in (50, 100)),
  completion_status text not null check (completion_status in ('incomplete', 'historical_partial', 'complete')),
  calculation_version integer not null default 1 check (calculation_version > 0),
  calculated_at timestamptz not null default now(),
  primary key (market_id, market_year, market_quarter),
  check (actual_company_count <= target_company_count),
  check (completion_status <> 'complete' or (actual_company_count = target_company_count and average_operating_income is not null and average_net_income is not null)),
  check ((operating_income_yoy_state = 'normal') = (operating_income_yoy_pct is not null)),
  check ((net_income_yoy_state = 'normal') = (net_income_yoy_pct is not null)),
  check ((operating_income_qoq_state = 'normal') = (operating_income_qoq_sa_pct is not null)),
  check ((net_income_qoq_state = 'normal') = (net_income_qoq_sa_pct is not null)),
  check (operating_income_yoy_state in ('normal', 'missing_prior', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening', 'loss_unchanged', 'from_zero')),
  check (net_income_yoy_state in ('normal', 'missing_prior', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening', 'loss_unchanged', 'from_zero')),
  check (operating_income_qoq_state in ('normal', 'missing_prior', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening', 'loss_unchanged', 'from_zero', 'insufficient_history')),
  check (net_income_qoq_state in ('normal', 'missing_prior', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening', 'loss_unchanged', 'from_zero', 'insufficient_history'))
);

create table earnings_v2.pipeline_state (
  source text not null,
  operation text not null,
  cursor jsonb not null default '{}'::jsonb,
  status text not null default 'ready' check (status in ('ready', 'running', 'failed')),
  last_success_at timestamptz,
  consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
  last_error text,
  updated_at timestamptz not null default now(),
  primary key (source, operation),
  check (last_error is null or length(last_error) <= 2000)
);

alter table earnings_v2.companies enable row level security;
alter table earnings_v2.company_identifiers enable row level security;
alter table earnings_v2.company_quarters enable row level security;
alter table earnings_v2.universe_members enable row level security;
alter table earnings_v2.market_quarters enable row level security;
alter table earnings_v2.pipeline_state enable row level security;

revoke all on all tables in schema earnings_v2 from public, anon, authenticated;
grant select, insert, update, delete on all tables in schema earnings_v2 to service_role;

-- Service-role-only RPC boundary. The private schema does not need to be
-- exposed through PostgREST, and callers cannot write arbitrary V2 tables.
create or replace function public.earnings_v2_upsert_companies(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.companies as target
    (company_id, country, company_name, reporting_currency, entity_kind, listed_from, delisted_on)
  select company_id, country, company_name, reporting_currency, entity_kind, listed_from, delisted_on
  from jsonb_populate_recordset(null::earnings_v2.companies, p_rows)
  on conflict (company_id) do update set
    country = excluded.country,
    company_name = excluded.company_name,
    reporting_currency = excluded.reporting_currency,
    entity_kind = excluded.entity_kind,
    listed_from = excluded.listed_from,
    delisted_on = excluded.delisted_on,
    updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_upsert_company_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.company_quarters as target (
    company_id, fiscal_year, fiscal_quarter, period_start, period_end, market_year, market_quarter,
    top_line, operating_income, net_income, currency, consolidation_scope, top_line_method,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date, quality_status, calculation_version
  )
  select
    company_id, fiscal_year, fiscal_quarter, period_start, period_end, market_year, market_quarter,
    top_line, operating_income, net_income, currency, consolidation_scope, top_line_method,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date, quality_status, coalesce(calculation_version, 1)
  from jsonb_populate_recordset(null::earnings_v2.company_quarters, p_rows)
  on conflict (company_id, fiscal_year, fiscal_quarter) do update set
    period_start = excluded.period_start,
    period_end = excluded.period_end,
    market_year = excluded.market_year,
    market_quarter = excluded.market_quarter,
    top_line = excluded.top_line,
    operating_income = excluded.operating_income,
    net_income = excluded.net_income,
    currency = excluded.currency,
    consolidation_scope = excluded.consolidation_scope,
    top_line_method = excluded.top_line_method,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct,
    net_income_yoy_state = excluded.net_income_yoy_state,
    operating_income_qoq_sa_pct = excluded.operating_income_qoq_sa_pct,
    operating_income_qoq_state = excluded.operating_income_qoq_state,
    net_income_qoq_sa_pct = excluded.net_income_qoq_sa_pct,
    net_income_qoq_state = excluded.net_income_qoq_state,
    source = excluded.source,
    source_filing_id = excluded.source_filing_id,
    filing_date = excluded.filing_date,
    revision_reference_date = excluded.revision_reference_date,
    quality_status = excluded.quality_status,
    calculation_version = excluded.calculation_version,
    updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_replace_universe(
  p_market_id text,
  p_market_year integer,
  p_market_quarter smallint,
  p_rows jsonb
)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  if p_market_id not in ('kr_largecap', 'kr_kosdaq', 'us_largecap', 'us_nasdaq') then
    raise exception 'Unsupported earnings V2 market: %', p_market_id;
  end if;
  delete from earnings_v2.universe_members
  where market_id = p_market_id and market_year = p_market_year and market_quarter = p_market_quarter;
  insert into earnings_v2.universe_members (
    market_id, market_year, market_quarter, reference_date, company_id,
    market_cap_rank, market_cap, currency, selection_method
  )
  select market_id, market_year, market_quarter, reference_date, company_id,
    market_cap_rank, market_cap, currency, selection_method
  from jsonb_populate_recordset(null::earnings_v2.universe_members, p_rows);
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_upsert_market_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.market_quarters as target (
    market_id, market_year, market_quarter, average_operating_income, average_net_income,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    actual_company_count, target_company_count, completion_status, calculation_version, calculated_at
  )
  select market_id, market_year, market_quarter, average_operating_income, average_net_income,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    actual_company_count, target_company_count, completion_status, coalesce(calculation_version, 1),
    coalesce(calculated_at, now())
  from jsonb_populate_recordset(null::earnings_v2.market_quarters, p_rows)
  on conflict (market_id, market_year, market_quarter) do update set
    average_operating_income = excluded.average_operating_income,
    average_net_income = excluded.average_net_income,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct,
    net_income_yoy_state = excluded.net_income_yoy_state,
    operating_income_qoq_sa_pct = excluded.operating_income_qoq_sa_pct,
    operating_income_qoq_state = excluded.operating_income_qoq_state,
    net_income_qoq_sa_pct = excluded.net_income_qoq_sa_pct,
    net_income_qoq_state = excluded.net_income_qoq_state,
    actual_company_count = excluded.actual_company_count,
    target_company_count = excluded.target_company_count,
    completion_status = excluded.completion_status,
    calculation_version = excluded.calculation_version,
    calculated_at = excluded.calculated_at;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_upsert_identifiers(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.company_identifiers as target (
    company_id, identifier_type, identifier_value, exchange, valid_from, valid_to, is_primary
  )
  select company_id, identifier_type, identifier_value, exchange,
    coalesce(valid_from, date '1900-01-01'), valid_to, coalesce(is_primary, false)
  from jsonb_populate_recordset(null::earnings_v2.company_identifiers, p_rows)
  on conflict (company_id, identifier_type, identifier_value, valid_from) do update set
    exchange = excluded.exchange,
    valid_to = excluded.valid_to,
    is_primary = excluded.is_primary;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_save_pipeline_state(
  p_source text,
  p_operation text,
  p_cursor jsonb,
  p_status text,
  p_last_success_at timestamptz default null,
  p_last_error text default null
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
begin
  insert into earnings_v2.pipeline_state (
    source, operation, cursor, status, last_success_at, consecutive_failures, last_error, updated_at
  ) values (
    p_source, p_operation, coalesce(p_cursor, '{}'::jsonb), p_status, p_last_success_at,
    case when p_status = 'failed' then 1 else 0 end, left(p_last_error, 2000), now()
  )
  on conflict (source, operation) do update set
    cursor = excluded.cursor,
    status = excluded.status,
    last_success_at = coalesce(excluded.last_success_at, earnings_v2.pipeline_state.last_success_at),
    consecutive_failures = case
      when excluded.status = 'failed' then earnings_v2.pipeline_state.consecutive_failures + 1
      else 0
    end,
    last_error = excluded.last_error,
    updated_at = now();
end;
$$;

create or replace function public.earnings_v2_get_company_quarters(p_company_id text)
returns setof earnings_v2.company_quarters
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select q.* from earnings_v2.company_quarters q
  where q.company_id = p_company_id
  order by q.fiscal_year, q.fiscal_quarter;
$$;

create or replace function public.earnings_v2_get_market_inputs(
  p_market_id text,
  p_market_year integer,
  p_market_quarter smallint
)
returns table (
  company_id text,
  market_cap_rank integer,
  operating_income numeric,
  net_income numeric,
  quality_status text
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select u.company_id, u.market_cap_rank, q.operating_income, q.net_income, q.quality_status
  from earnings_v2.universe_members u
  left join earnings_v2.company_quarters q
    on q.company_id = u.company_id
   and q.market_year = u.market_year
   and q.market_quarter = u.market_quarter
  where u.market_id = p_market_id
    and u.market_year = p_market_year
    and u.market_quarter = p_market_quarter
  order by u.market_cap_rank;
$$;

revoke all on function public.earnings_v2_upsert_companies(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_upsert_identifiers(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_upsert_company_quarters(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_replace_universe(text, integer, smallint, jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_upsert_market_quarters(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_get_company_quarters(text) from public, anon, authenticated;
revoke all on function public.earnings_v2_get_market_inputs(text, integer, smallint) from public, anon, authenticated;
revoke all on function public.earnings_v2_save_pipeline_state(text, text, jsonb, text, timestamptz, text) from public, anon, authenticated;
grant execute on function public.earnings_v2_upsert_companies(jsonb) to service_role;
grant execute on function public.earnings_v2_upsert_identifiers(jsonb) to service_role;
grant execute on function public.earnings_v2_upsert_company_quarters(jsonb) to service_role;
grant execute on function public.earnings_v2_replace_universe(text, integer, smallint, jsonb) to service_role;
grant execute on function public.earnings_v2_upsert_market_quarters(jsonb) to service_role;
grant execute on function public.earnings_v2_get_company_quarters(text) to service_role;
grant execute on function public.earnings_v2_get_market_inputs(text, integer, smallint) to service_role;
grant execute on function public.earnings_v2_save_pipeline_state(text, text, jsonb, text, timestamptz, text) to service_role;

comment on schema earnings_v2 is 'Independent, minimal MacroWatch earnings V2 storage. No V1 dependencies.';
comment on table earnings_v2.company_quarters is 'Single-quarter source facts plus UI-ready YoY and seasonally adjusted QoQ values.';
comment on table earnings_v2.universe_members is 'Only final point-in-time market universe members; intermediate candidate ranks are not persisted.';
