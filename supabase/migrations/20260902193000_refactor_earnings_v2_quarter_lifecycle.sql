-- Safe V6 rollout. The old V2 columns/RPCs remain readable during the pilot,
-- while the collector writes only through the explicitly versioned V6 RPCs.
-- After V6 is verified, legacy columns can be removed in one reviewed cleanup.

alter table earnings_v2.company_quarters
  add column if not exists is_pending boolean;

update earnings_v2.company_quarters
set is_pending = top_line is null or operating_income is null or net_income is null
where is_pending is null;

alter table earnings_v2.company_quarters
  alter column is_pending set default true,
  alter column is_pending set not null;

alter table earnings_v2.company_quarters
  drop constraint if exists company_quarters_pending_values_check;
alter table earnings_v2.company_quarters
  add constraint company_quarters_pending_values_check
  check (is_pending or (top_line is not null and operating_income is not null and net_income is not null));

create index if not exists earnings_v2_company_quarters_pending
  on earnings_v2.company_quarters(market_year, market_quarter, company_id)
  where is_pending;

comment on column earnings_v2.company_quarters.is_pending is
  'Excludes the entire company row from market aggregation until all required amounts are available.';

alter table earnings_v2.market_quarters
  add column if not exists reference_date date,
  add column if not exists top_line_total numeric(38, 4),
  add column if not exists operating_income_total numeric(38, 4),
  add column if not exists net_income_total numeric(38, 4),
  add column if not exists reported_company_count integer,
  add column if not exists pending_company_count integer,
  add column if not exists lifecycle_status text;

update earnings_v2.market_quarters
set reference_date = coalesce(
      reference_date,
      (make_date(market_year, market_quarter * 3, 1) + interval '1 month - 1 day')::date
    ),
    reported_company_count = coalesce(reported_company_count, actual_company_count),
    pending_company_count = coalesce(pending_company_count, target_company_count - actual_company_count),
    -- Legacy rows contain average-based pilot values, not V6 totals. They remain
    -- readable but re-enter V6 as collecting until the new aggregator rewrites them.
    lifecycle_status = coalesce(lifecycle_status, 'collecting');

alter table earnings_v2.market_quarters
  alter column reference_date set not null,
  alter column reported_company_count set not null,
  alter column pending_company_count set not null,
  alter column lifecycle_status set default 'collecting',
  alter column lifecycle_status set not null;

alter table earnings_v2.market_quarters
  drop constraint if exists market_quarters_v6_counts_check,
  drop constraint if exists market_quarters_v6_lifecycle_check,
  drop constraint if exists market_quarters_v6_complete_check,
  drop constraint if exists market_quarters_v6_provisional_check;
alter table earnings_v2.market_quarters
  add constraint market_quarters_v6_counts_check
    check (reported_company_count >= 0 and pending_company_count >= 0 and
           reported_company_count + pending_company_count = target_company_count),
  add constraint market_quarters_v6_lifecycle_check
    check (lifecycle_status in ('collecting', 'provisional', 'complete')),
  add constraint market_quarters_v6_complete_check
    check (lifecycle_status <> 'complete' or (
      reported_company_count = target_company_count and pending_company_count = 0 and
      top_line_total is not null and operating_income_total is not null and net_income_total is not null
    )),
  add constraint market_quarters_v6_provisional_check
    check (lifecycle_status <> 'provisional' or (
      top_line_total is not null and operating_income_total is not null and net_income_total is not null
    ));

create or replace function public.earnings_v2_v6_upsert_company_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.company_quarters as target (
    company_id, fiscal_year, fiscal_quarter, period_start, period_end, market_year, market_quarter,
    top_line, operating_income, net_income, operating_margin_pct, net_margin_pct,
    currency, consolidation_scope, is_pending,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date, quality_status, calculation_version
  )
  select company_id, fiscal_year, fiscal_quarter, period_start, period_end, market_year, market_quarter,
    top_line, operating_income, net_income, operating_margin_pct, net_margin_pct,
    currency, consolidation_scope, coalesce(is_pending, true),
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date,
    case when coalesce(is_pending, true) then 'review_required' else 'complete' end,
    coalesce(calculation_version, 6)
  from jsonb_populate_recordset(null::earnings_v2.company_quarters, p_rows)
  on conflict (company_id, fiscal_year, fiscal_quarter) do update set
    period_start = excluded.period_start, period_end = excluded.period_end,
    market_year = excluded.market_year, market_quarter = excluded.market_quarter,
    top_line = excluded.top_line, operating_income = excluded.operating_income, net_income = excluded.net_income,
    operating_margin_pct = excluded.operating_margin_pct, net_margin_pct = excluded.net_margin_pct,
    currency = excluded.currency, consolidation_scope = excluded.consolidation_scope,
    is_pending = excluded.is_pending, quality_status = excluded.quality_status,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct, net_income_yoy_state = excluded.net_income_yoy_state,
    operating_income_qoq_sa_pct = excluded.operating_income_qoq_sa_pct,
    operating_income_qoq_state = excluded.operating_income_qoq_state,
    net_income_qoq_sa_pct = excluded.net_income_qoq_sa_pct,
    net_income_qoq_state = excluded.net_income_qoq_state,
    source = excluded.source, source_filing_id = excluded.source_filing_id,
    filing_date = excluded.filing_date, revision_reference_date = excluded.revision_reference_date,
    calculation_version = excluded.calculation_version, updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_v6_upsert_market_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.market_quarters as target (
    market_id, market_year, market_quarter, reference_date,
    top_line_total, operating_income_total, net_income_total,
    average_operating_income, average_net_income, operating_margin_pct, net_margin_pct,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    actual_company_count, reported_company_count, pending_company_count, target_company_count,
    completion_status, lifecycle_status, calculation_version, calculated_at
  )
  select market_id, market_year, market_quarter, reference_date,
    top_line_total, operating_income_total, net_income_total,
    operating_income_total / nullif(target_company_count, 0),
    net_income_total / nullif(target_company_count, 0),
    operating_margin_pct, net_margin_pct,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    reported_company_count, reported_company_count, pending_company_count, target_company_count,
    case when lifecycle_status = 'complete' then 'complete' else 'incomplete' end,
    lifecycle_status, coalesce(calculation_version, 6), coalesce(calculated_at, now())
  from jsonb_populate_recordset(null::earnings_v2.market_quarters, p_rows)
  on conflict (market_id, market_year, market_quarter) do update set
    reference_date = excluded.reference_date,
    top_line_total = excluded.top_line_total,
    operating_income_total = excluded.operating_income_total,
    net_income_total = excluded.net_income_total,
    average_operating_income = excluded.average_operating_income,
    average_net_income = excluded.average_net_income,
    operating_margin_pct = excluded.operating_margin_pct,
    net_margin_pct = excluded.net_margin_pct,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct,
    net_income_yoy_state = excluded.net_income_yoy_state,
    operating_income_qoq_sa_pct = excluded.operating_income_qoq_sa_pct,
    operating_income_qoq_state = excluded.operating_income_qoq_state,
    net_income_qoq_sa_pct = excluded.net_income_qoq_sa_pct,
    net_income_qoq_state = excluded.net_income_qoq_state,
    actual_company_count = excluded.actual_company_count,
    reported_company_count = excluded.reported_company_count,
    pending_company_count = excluded.pending_company_count,
    target_company_count = excluded.target_company_count,
    completion_status = excluded.completion_status,
    lifecycle_status = excluded.lifecycle_status,
    calculation_version = excluded.calculation_version,
    calculated_at = excluded.calculated_at;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_get_universe(
  p_market_id text, p_market_year integer, p_market_quarter smallint
)
returns setof earnings_v2.universe_members
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select u.* from earnings_v2.universe_members u
  where u.market_id = p_market_id
    and u.market_year = p_market_year
    and u.market_quarter = p_market_quarter
  order by u.market_cap_rank;
$$;

create or replace function public.earnings_v2_v6_get_market_inputs(
  p_market_id text, p_market_year integer, p_market_quarter smallint
)
returns table (
  company_id text, market_cap_rank integer, top_line numeric,
  operating_income numeric, net_income numeric, is_pending boolean
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select u.company_id, u.market_cap_rank, q.top_line,
    q.operating_income, q.net_income, coalesce(q.is_pending, true)
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

revoke all on function public.earnings_v2_v6_upsert_company_quarters(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_v6_upsert_market_quarters(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_get_universe(text, integer, smallint) from public, anon, authenticated;
revoke all on function public.earnings_v2_v6_get_market_inputs(text, integer, smallint) from public, anon, authenticated;
grant execute on function public.earnings_v2_v6_upsert_company_quarters(jsonb) to service_role;
grant execute on function public.earnings_v2_v6_upsert_market_quarters(jsonb) to service_role;
grant execute on function public.earnings_v2_get_universe(text, integer, smallint) to service_role;
grant execute on function public.earnings_v2_v6_get_market_inputs(text, integer, smallint) to service_role;

comment on column earnings_v2.market_quarters.lifecycle_status is
  'V6 lifecycle: collecting without a complete baseline, provisional with placeholders, or complete with current actual facts.';
