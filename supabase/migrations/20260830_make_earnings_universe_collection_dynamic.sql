-- Make collection coverage follow effective-dated memberships instead of a
-- fixed union count. Reentries extend the normal ten-year fetch window back to
-- the last complete quarter so an arbitrarily long inactive gap is repaired.

create or replace function public.get_current_earnings_collection_coverage(p_country text)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  v_country text := upper(trim(coalesce(p_country, '')));
  v_result jsonb;
begin
  if v_country not in ('KR', 'US') then
    raise exception 'Unsupported earnings collection country';
  end if;

  with active_indices as (
    select index_id, target_count
    from public.earnings_indices
    where country = v_country and is_active
  ), index_coverage as (
    select i.index_id, i.target_count,
           count(m.company_id)::integer as active_membership_count
    from active_indices i
    left join public.earnings_index_memberships m
      on m.index_id = i.index_id and m.effective_to is null
    group by i.index_id, i.target_count
  ), current_companies as (
    select distinct m.company_id
    from public.earnings_index_memberships m
    join active_indices i on i.index_id = m.index_id
    where m.effective_to is null
  ), identifier_coverage as (
    select cc.company_id, c.ticker,
           exists (
             select 1
             from public.earnings_company_identifiers id
             where id.company_id = cc.company_id
               and id.identifier_type = case when v_country = 'KR' then 'dart_corp_code' else 'sec_cik' end
               and id.valid_to is null
           ) as has_identifier
    from current_companies cc
    join public.earnings_companies c on c.id = cc.company_id
  )
  select jsonb_build_object(
    'country', v_country,
    'indices', coalesce((
      select jsonb_agg(jsonb_build_object(
        'index_id', index_id,
        'target_count', target_count,
        'active_membership_count', active_membership_count
      ) order by index_id)
      from index_coverage
    ), '[]'::jsonb),
    'unique_companies', (select count(*) from current_companies),
    'companies_with_identifier', (
      select count(*) from identifier_coverage where has_identifier
    ),
    'missing_identifier_tickers', coalesce((
      select jsonb_agg(ticker order by ticker)
      from identifier_coverage where not has_identifier
    ), '[]'::jsonb)
  ) into v_result;
  return v_result;
end;
$$;

revoke all on function public.get_current_earnings_collection_coverage(text)
  from public, anon, authenticated;
grant execute on function public.get_current_earnings_collection_coverage(text)
  to service_role;

drop function if exists public.list_current_sec_earnings_companies();
create function public.list_current_sec_earnings_companies()
returns table(
  company_id uuid,
  cik text,
  ticker text,
  first_collection_year integer
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  with current_companies as (
    select distinct companies.id, companies.ticker
    from public.earnings_companies companies
    join public.earnings_index_memberships memberships
      on memberships.company_id = companies.id and memberships.effective_to is null
    join public.earnings_indices indices
      on indices.index_id = memberships.index_id
     and indices.country = 'US' and indices.is_active
    where companies.country = 'US'
  ), last_complete as (
    select q.company_id, max(q.fiscal_year)::integer as fiscal_year
    from public.earnings_quarterly_financials q
    where q.quality_status = 'complete'
      and q.revenue is not null
      and q.operating_income is not null
      and q.net_income is not null
    group by q.company_id
  )
  select cc.id, identifiers.identifier_value, cc.ticker,
         greatest(
           2009,
           least(
             extract(year from current_date)::integer - 9,
             coalesce(lc.fiscal_year, extract(year from current_date)::integer - 9)
           )
         ) as first_collection_year
  from current_companies cc
  join public.earnings_company_identifiers identifiers
    on identifiers.company_id = cc.id
   and identifiers.identifier_type = 'sec_cik'
   and identifiers.valid_to is null
  left join last_complete lc on lc.company_id = cc.id
  order by cc.id, identifiers.is_primary desc, identifiers.identifier_value;
$$;

revoke all on function public.list_current_sec_earnings_companies()
  from public, anon, authenticated;
grant execute on function public.list_current_sec_earnings_companies()
  to service_role;

create or replace function public.enqueue_earnings_open_dart_backfill(
  p_as_of_year integer,
  p_years integer default 10
)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_inserted integer;
begin
  if p_as_of_year < 2015 or p_as_of_year > 2200 or p_years < 1 or p_years > 20 then
    raise exception 'Invalid OpenDART backfill window';
  end if;

  with active_companies as (
    select distinct memberships.company_id
    from public.earnings_index_memberships memberships
    join public.earnings_indices indices on indices.index_id = memberships.index_id
    where memberships.effective_to is null
      and indices.country = 'KR' and indices.is_active
  ), last_complete as (
    select ac.company_id,
           max(q.fiscal_year * 4 + q.fiscal_quarter - 1)::integer as period_index
    from active_companies ac
    left join public.earnings_quarterly_financials q
      on q.company_id = ac.company_id
     and q.quality_status = 'complete'
     and q.revenue is not null
     and q.operating_income is not null
     and q.net_income is not null
    group by ac.company_id
  ), bounds as (
    select company_id, period_index,
           least(
             (p_as_of_year - p_years + 1) * 4,
             coalesce(period_index + 1, (p_as_of_year - p_years + 1) * 4)
           ) as first_period_index
    from last_complete
  ), candidate_periods as (
    select b.company_id, b.period_index as last_complete_period_index,
           value as candidate_index,
           value / 4 as business_year,
           case mod(value, 4)
             when 0 then '11013'
             when 1 then '11012'
             when 2 then '11014'
             else '11011'
           end as report_code,
           mod(value, 4) + 1 as fiscal_quarter
    from bounds b
    cross join lateral generate_series(
      b.first_period_index,
      p_as_of_year * 4 + 3
    ) value
  )
  insert into public.earnings_ingestion_jobs
    (source, job_kind, company_id, business_year, report_code, reason, priority, metadata)
  select 'open_dart', 'financial_period', cp.company_id, cp.business_year,
         cp.report_code,
         case when cp.last_complete_period_index is null then 'new_company' else 'reentry_gap' end,
         case when cp.last_complete_period_index is null then 20 else 60 end,
         jsonb_build_object('dynamic_backfill', true)
  from candidate_periods cp
  where exists (
    select 1 from public.earnings_company_identifiers identifiers
    where identifiers.company_id = cp.company_id
      and identifiers.identifier_type = 'dart_corp_code'
      and identifiers.valid_to is null
  )
  and not exists (
    select 1 from public.earnings_quarterly_financials q
    where q.company_id = cp.company_id
      and q.fiscal_year = cp.business_year
      and q.fiscal_quarter = cp.fiscal_quarter
      and q.quality_status = 'complete'
      and q.revenue is not null
      and q.operating_income is not null
      and q.net_income is not null
  )
  and not exists (
    select 1 from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart'
      and jobs.job_kind = 'financial_period'
      and jobs.company_id = cp.company_id
      and jobs.business_year = cp.business_year
      and jobs.report_code = cp.report_code
      and (
        jobs.status in ('pending', 'running', 'retry')
        or jobs.created_at >= now() - interval '30 days'
      )
  );
  get diagnostics v_inserted = row_count;
  return v_inserted;
end;
$$;

revoke all on function public.enqueue_earnings_open_dart_backfill(integer, integer)
  from public, anon, authenticated;
grant execute on function public.enqueue_earnings_open_dart_backfill(integer, integer)
  to service_role;
