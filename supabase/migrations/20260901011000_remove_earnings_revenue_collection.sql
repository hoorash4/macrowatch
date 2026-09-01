-- Earnings Momentum deliberately compares operating income and net income only.
-- Keep the legacy revenue column/data for rollback and historical audit, but do
-- not accept, require, overwrite, or derive revenue in any active ingestion path.

create or replace function public.complete_earnings_open_dart_job(
  p_job_id bigint,
  p_filing jsonb,
  p_quarter jsonb default null,
  p_outcome text default 'complete'
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_job public.earnings_ingestion_jobs%rowtype;
  v_filing_id uuid;
  v_previous_filing_id uuid;
  v_existing_version integer;
  v_missing text[];
  v_quality text;
begin
  if p_outcome not in ('complete', 'review_required', 'no_data') then
    raise exception 'Invalid OpenDART job outcome';
  end if;

  select * into v_job
  from public.earnings_ingestion_jobs
  where id = p_job_id and source = 'open_dart'
  for update;
  if v_job.id is null or v_job.status <> 'running' then
    raise exception 'OpenDART job is not currently claimed';
  end if;

  if p_outcome = 'no_data' then
    update public.earnings_ingestion_jobs
    set status = 'completed', completed_at = now(), claimed_at = null,
        metadata = metadata || jsonb_build_object('outcome', p_outcome), updated_at = now()
    where id = p_job_id;
    return jsonb_build_object('job_id', p_job_id, 'outcome', p_outcome);
  end if;

  if nullif(trim(coalesce(p_filing->>'source_filing_id', '')), '') is null then
    raise exception 'OpenDART filing identity is required';
  end if;

  select filings.id into v_previous_filing_id
  from public.earnings_filings filings
  where filings.company_id = v_job.company_id and filings.source = 'open_dart'
    and filings.fiscal_year = v_job.business_year
    and filings.fiscal_quarter = (p_filing->>'fiscal_quarter')::smallint
    and filings.source_filing_id <> p_filing->>'source_filing_id'
  order by filings.filing_date desc, filings.created_at desc
  limit 1;

  insert into public.earnings_filings (
    company_id, source, source_filing_id, filing_kind, source_report_code,
    fiscal_year, fiscal_quarter, market_year, market_quarter,
    period_start, period_end, filing_date, filing_at, is_correction,
    corrects_filing_id, source_url, metadata, updated_at
  ) values (
    v_job.company_id, 'open_dart', p_filing->>'source_filing_id',
    p_filing->>'filing_kind', v_job.report_code,
    v_job.business_year, (p_filing->>'fiscal_quarter')::smallint,
    (p_filing->>'market_year')::integer, (p_filing->>'market_quarter')::smallint,
    nullif(p_filing->>'period_start', '')::date, (p_filing->>'period_end')::date,
    (p_filing->>'filing_date')::date, nullif(p_filing->>'filing_at', '')::timestamptz,
    coalesce((p_filing->>'is_correction')::boolean, false),
    case when coalesce((p_filing->>'is_correction')::boolean, false)
         then v_previous_filing_id else null end,
    nullif(p_filing->>'source_url', ''), coalesce(p_filing->'metadata', '{}'::jsonb), now()
  )
  on conflict (source, source_filing_id) do update set
    metadata = excluded.metadata,
    updated_at = now()
  returning id into v_filing_id;

  if p_quarter is not null then
    v_missing := coalesce(array(
      select jsonb_array_elements_text(coalesce(p_quarter->'missing_metrics', '[]'::jsonb))
    ), '{}'::text[]);
    if not v_missing <@ array['operating_income', 'net_income']::text[] then
      raise exception 'Unknown profit metric in missing_metrics';
    end if;
    if nullif(p_quarter->>'operating_income', '') is null
       and nullif(p_quarter->>'net_income', '') is null then
      raise exception 'At least one canonical profit metric is required';
    end if;
    v_quality := case when cardinality(v_missing) = 0 then 'complete' else 'partial' end;

    select canonical_version into v_existing_version
    from public.earnings_quarterly_financials
    where company_id = v_job.company_id
      and fiscal_year = v_job.business_year
      and fiscal_quarter = (p_quarter->>'fiscal_quarter')::smallint;

    insert into public.earnings_quarterly_financials (
      company_id, fiscal_year, fiscal_quarter, market_year, market_quarter,
      period_start, period_end, operating_income, net_income,
      currency, consolidation_scope, source_filing_id, quality_status,
      missing_metrics, canonical_version, source_updated_at, calculated_at, updated_at
    ) values (
      v_job.company_id, v_job.business_year, (p_quarter->>'fiscal_quarter')::smallint,
      (p_quarter->>'market_year')::integer, (p_quarter->>'market_quarter')::smallint,
      nullif(p_quarter->>'period_start', '')::date, (p_quarter->>'period_end')::date,
      nullif(p_quarter->>'operating_income', '')::numeric,
      nullif(p_quarter->>'net_income', '')::numeric,
      p_quarter->>'currency', p_quarter->>'consolidation_scope', v_filing_id,
      v_quality, v_missing, coalesce(v_existing_version, 0) + 1,
      (p_quarter->>'source_updated_at')::timestamptz, now(), now()
    ) on conflict (company_id, fiscal_year, fiscal_quarter) do update set
      market_year = excluded.market_year, market_quarter = excluded.market_quarter,
      period_start = excluded.period_start, period_end = excluded.period_end,
      operating_income = excluded.operating_income, net_income = excluded.net_income,
      currency = excluded.currency, consolidation_scope = excluded.consolidation_scope,
      source_filing_id = excluded.source_filing_id,
      quality_status = excluded.quality_status, missing_metrics = excluded.missing_metrics,
      canonical_version = public.earnings_quarterly_financials.canonical_version + 1,
      source_updated_at = excluded.source_updated_at, calculated_at = now(), updated_at = now();
  end if;

  update public.earnings_ingestion_jobs
  set status = 'completed', completed_at = now(), claimed_at = null,
      metadata = metadata || jsonb_build_object('outcome', p_outcome, 'filing_id', v_filing_id),
      updated_at = now()
  where id = p_job_id;

  return jsonb_build_object(
    'job_id', p_job_id, 'filing_id', v_filing_id,
    'outcome', p_outcome, 'canonical_updated', p_quarter is not null
  );
end;
$$;

create or replace function public.upsert_sec_company_quarters(
  p_company_id uuid,
  p_rows jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_row jsonb;
  v_filing jsonb;
  v_quarter jsonb;
  v_filing_id uuid;
  v_previous_filing_id uuid;
  v_existing_version integer;
  v_changed integer := 0;
  v_seen integer := 0;
  v_is_correction boolean;
begin
  if not exists (
    select 1 from public.earnings_companies
    where id = p_company_id and country = 'US'
  ) then
    raise exception 'Unknown U.S. earnings company';
  end if;
  if jsonb_typeof(p_rows) <> 'array' then
    raise exception 'SEC quarter rows must be an array';
  end if;

  for v_row in select value from jsonb_array_elements(p_rows)
  loop
    v_filing := v_row->'filing';
    v_quarter := v_row->'quarter';
    if nullif(trim(coalesce(v_filing->>'source_filing_id', '')), '') is null
       or (v_filing->>'fiscal_year')::integer not between 1900 and 2200
       or (v_filing->>'fiscal_quarter')::integer not between 1 and 4
       or nullif(v_quarter->>'operating_income', '') is null
       or nullif(v_quarter->>'net_income', '') is null then
      raise exception 'Incomplete SEC profit row';
    end if;

    select filings.id into v_previous_filing_id
    from public.earnings_filings filings
    where filings.company_id = p_company_id
      and filings.source = 'sec_edgar'
      and filings.fiscal_year = (v_filing->>'fiscal_year')::integer
      and filings.fiscal_quarter = (v_filing->>'fiscal_quarter')::smallint
      and filings.source_filing_id <> v_filing->>'source_filing_id'
    order by filings.filing_date desc, filings.created_at desc
    limit 1;
    v_is_correction := coalesce((v_filing->>'is_correction')::boolean, false)
      and v_previous_filing_id is not null;

    insert into public.earnings_filings (
      company_id, source, source_filing_id, filing_kind, source_report_code,
      fiscal_year, fiscal_quarter, market_year, market_quarter,
      period_start, period_end, filing_date, is_correction,
      corrects_filing_id, source_url, metadata, updated_at
    ) values (
      p_company_id, 'sec_edgar', v_filing->>'source_filing_id',
      case when v_is_correction then 'amendment' else v_filing->>'filing_kind' end,
      v_filing->'metadata'->>'form',
      (v_filing->>'fiscal_year')::integer, (v_filing->>'fiscal_quarter')::smallint,
      (v_filing->>'market_year')::integer, (v_filing->>'market_quarter')::smallint,
      (v_filing->>'period_start')::date, (v_filing->>'period_end')::date,
      (v_filing->>'filing_date')::date, v_is_correction,
      case when v_is_correction then v_previous_filing_id else null end,
      nullif(v_filing->>'source_url', ''), coalesce(v_filing->'metadata', '{}'::jsonb), now()
    ) on conflict (source, source_filing_id) do update set
      metadata = excluded.metadata,
      updated_at = now()
    returning id into v_filing_id;

    select canonical_version into v_existing_version
    from public.earnings_quarterly_financials
    where company_id = p_company_id
      and fiscal_year = (v_quarter->>'fiscal_year')::integer
      and fiscal_quarter = (v_quarter->>'fiscal_quarter')::smallint;

    insert into public.earnings_quarterly_financials (
      company_id, fiscal_year, fiscal_quarter, market_year, market_quarter,
      period_start, period_end, operating_income, net_income,
      currency, consolidation_scope, source_filing_id, quality_status,
      missing_metrics, canonical_version, source_updated_at, calculated_at, updated_at
    ) values (
      p_company_id, (v_quarter->>'fiscal_year')::integer,
      (v_quarter->>'fiscal_quarter')::smallint,
      (v_quarter->>'market_year')::integer, (v_quarter->>'market_quarter')::smallint,
      (v_quarter->>'period_start')::date, (v_quarter->>'period_end')::date,
      (v_quarter->>'operating_income')::numeric, (v_quarter->>'net_income')::numeric,
      'USD', 'NA', v_filing_id, 'complete', '{}'::text[],
      coalesce(v_existing_version, 0) + 1,
      (v_quarter->>'source_updated_at')::timestamptz, now(), now()
    ) on conflict (company_id, fiscal_year, fiscal_quarter) do update set
      market_year = excluded.market_year,
      market_quarter = excluded.market_quarter,
      period_start = excluded.period_start,
      period_end = excluded.period_end,
      operating_income = excluded.operating_income,
      net_income = excluded.net_income,
      currency = 'USD', consolidation_scope = 'NA',
      source_filing_id = excluded.source_filing_id,
      quality_status = 'complete', missing_metrics = '{}'::text[],
      canonical_version = public.earnings_quarterly_financials.canonical_version + 1,
      source_updated_at = excluded.source_updated_at,
      calculated_at = now(), updated_at = now()
    where public.earnings_quarterly_financials.source_filing_id is distinct from excluded.source_filing_id
       or public.earnings_quarterly_financials.operating_income is distinct from excluded.operating_income
       or public.earnings_quarterly_financials.net_income is distinct from excluded.net_income;
    if found then v_changed := v_changed + 1; end if;
    v_seen := v_seen + 1;
  end loop;

  return jsonb_build_object('company_id', p_company_id, 'seen', v_seen, 'changed', v_changed);
end;
$$;

-- Rows that were partial only because revenue was absent are complete under
-- the new two-profit-metric contract. Existing revenue values remain intact.
update public.earnings_quarterly_financials
set missing_metrics = array_remove(coalesce(missing_metrics, '{}'::text[]), 'revenue'),
    quality_status = case
      when cardinality(array_remove(coalesce(missing_metrics, '{}'::text[]), 'revenue')) = 0
      then 'complete'
      else quality_status
    end,
    updated_at = now()
where 'revenue' = any(coalesce(missing_metrics, '{}'::text[]));

-- Revenue-only repair jobs are obsolete. Complete them without deleting audit
-- history or affecting ordinary earnings ingestion jobs.
update public.earnings_ingestion_jobs
set status = 'completed',
    completed_at = coalesce(completed_at, now()),
    claimed_at = null,
    last_error = null,
    metadata = coalesce(metadata, '{}'::jsonb)
      || jsonb_build_object('outcome', 'revenue_collection_removed'),
    updated_at = now()
where source = 'open_dart'
  and metadata->>'revenue_repair' = 'true'
  and status in ('pending', 'retry', 'running');

revoke all on function public.complete_earnings_open_dart_job(bigint, jsonb, jsonb, text) from public;
revoke all on function public.upsert_sec_company_quarters(uuid, jsonb) from public;
grant execute on function public.complete_earnings_open_dart_job(bigint, jsonb, jsonb, text) to service_role;
grant execute on function public.upsert_sec_company_quarters(uuid, jsonb) to service_role;
