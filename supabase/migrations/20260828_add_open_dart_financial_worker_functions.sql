-- Atomic queue and persistence operations for the OpenDART financial worker.
-- HTTP calls happen outside the transaction. Once a response is interpreted,
-- one RPC preserves the filing/facts, updates the canonical quarter only when
-- all required metrics are complete, and closes the queue job together.

create or replace function public.claim_earnings_open_dart_jobs(p_limit integer default 100)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_year integer;
  v_report_code text;
  v_result jsonb;
begin
  if p_limit < 1 or p_limit > 100 then
    raise exception 'OpenDART claim limit must be between 1 and 100';
  end if;

  -- A terminated runner must not strand work permanently.
  update public.earnings_ingestion_jobs
  set status = 'retry', claimed_at = null, available_at = now(), updated_at = now(),
      last_error = 'Worker lease expired'
  where source = 'open_dart' and status = 'running'
    and claimed_at < now() - interval '30 minutes';

  select jobs.business_year, jobs.report_code
    into v_year, v_report_code
  from public.earnings_ingestion_jobs jobs
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
    and jobs.attempts < jobs.max_attempts
    and exists (
      select 1 from public.earnings_company_identifiers identifiers
      where identifiers.company_id = jobs.company_id
        and identifiers.identifier_type = 'dart_corp_code'
        and identifiers.valid_to is null
    )
  order by jobs.priority desc, jobs.available_at asc, jobs.id asc
  for update skip locked
  limit 1;

  if v_year is null then
    return '[]'::jsonb;
  end if;

  with candidates as (
    select jobs.id
    from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
      and jobs.attempts < jobs.max_attempts
      and jobs.business_year = v_year and jobs.report_code = v_report_code
      and exists (
        select 1 from public.earnings_company_identifiers identifiers
        where identifiers.company_id = jobs.company_id
          and identifiers.identifier_type = 'dart_corp_code'
          and identifiers.valid_to is null
      )
    order by jobs.priority desc, jobs.available_at asc, jobs.id asc
    for update skip locked
    limit p_limit
  ), claimed as (
    update public.earnings_ingestion_jobs jobs
    set status = 'running', attempts = attempts + 1, claimed_at = now(),
        last_error = null, updated_at = now()
    from candidates
    where jobs.id = candidates.id
    returning jobs.*
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', claimed.id,
    'company_id', claimed.company_id,
    'business_year', claimed.business_year,
    'report_code', claimed.report_code,
    'reason', claimed.reason,
    'attempts', claimed.attempts,
    'metadata', claimed.metadata,
    'corp_code', identifiers.identifier_value,
    'ticker', companies.ticker,
    'company_name', companies.company_name
  ) order by claimed.priority desc, claimed.id asc), '[]'::jsonb)
  into v_result
  from claimed
  join public.earnings_companies companies on companies.id = claimed.company_id
  join public.earnings_company_identifiers identifiers
    on identifiers.company_id = claimed.company_id
   and identifiers.identifier_type = 'dart_corp_code'
   and identifiers.valid_to is null;

  return v_result;
end;
$$;

revoke all on function public.claim_earnings_open_dart_jobs(integer)
  from public, anon, authenticated;
grant execute on function public.claim_earnings_open_dart_jobs(integer)
  to service_role;

create or replace function public.complete_earnings_open_dart_job(
  p_job_id bigint,
  p_source_payload_id uuid,
  p_filing jsonb,
  p_facts jsonb,
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
  v_fact jsonb;
  v_missing text[];
begin
  if p_outcome not in ('complete', 'review_required', 'no_data') then
    raise exception 'Invalid OpenDART job outcome';
  end if;
  if jsonb_typeof(coalesce(p_facts, '[]'::jsonb)) <> 'array' then
    raise exception 'OpenDART facts must be a JSON array';
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
    corrects_filing_id, source_url, metadata, source_payload_id, updated_at
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
    nullif(p_filing->>'source_url', ''), coalesce(p_filing->'metadata', '{}'::jsonb),
    p_source_payload_id, now()
  )
  on conflict (source, source_filing_id) do update set
    metadata = excluded.metadata,
    source_payload_id = coalesce(excluded.source_payload_id, public.earnings_filings.source_payload_id),
    updated_at = now()
  returning id into v_filing_id;

  for v_fact in select value from jsonb_array_elements(coalesce(p_facts, '[]'::jsonb))
  loop
    insert into public.earnings_financial_facts (
      filing_id, company_id, metric, source_account_id, source_account_name,
      statement_type, consolidation_scope, period_start, period_end,
      value_kind, value, currency, source_field, source_row_key, raw_row,
      source_payload_id
    ) values (
      v_filing_id, v_job.company_id, v_fact->>'metric',
      nullif(v_fact->>'source_account_id', ''), v_fact->>'source_account_name',
      nullif(v_fact->>'statement_type', ''), v_fact->>'consolidation_scope',
      nullif(v_fact->>'period_start', '')::date, (v_fact->>'period_end')::date,
      v_fact->>'value_kind', nullif(v_fact->>'value', '')::numeric,
      nullif(v_fact->>'currency', ''), nullif(v_fact->>'source_field', ''),
      v_fact->>'source_row_key', v_fact->'raw_row',
      coalesce(nullif(v_fact->>'source_payload_id', '')::uuid, p_source_payload_id)
    ) on conflict (filing_id, source_row_key) do nothing;
  end loop;

  if p_quarter is not null then
    v_missing := coalesce(array(
      select jsonb_array_elements_text(coalesce(p_quarter->'missing_metrics', '[]'::jsonb))
    ), '{}'::text[]);
    if cardinality(v_missing) <> 0 then
      raise exception 'Incomplete quarter cannot replace canonical financials';
    end if;

    select canonical_version into v_existing_version
    from public.earnings_quarterly_financials
    where company_id = v_job.company_id
      and fiscal_year = v_job.business_year
      and fiscal_quarter = (p_quarter->>'fiscal_quarter')::smallint;

    insert into public.earnings_quarterly_financials (
      company_id, fiscal_year, fiscal_quarter, market_year, market_quarter,
      period_start, period_end, revenue, operating_income, net_income, eps,
      currency, consolidation_scope, source_filing_id, quality_status,
      missing_metrics, canonical_version, source_updated_at, calculated_at, updated_at
    ) values (
      v_job.company_id, v_job.business_year, (p_quarter->>'fiscal_quarter')::smallint,
      (p_quarter->>'market_year')::integer, (p_quarter->>'market_quarter')::smallint,
      nullif(p_quarter->>'period_start', '')::date, (p_quarter->>'period_end')::date,
      (p_quarter->>'revenue')::numeric, (p_quarter->>'operating_income')::numeric,
      (p_quarter->>'net_income')::numeric, (p_quarter->>'eps')::numeric,
      p_quarter->>'currency', p_quarter->>'consolidation_scope', v_filing_id,
      'complete', '{}'::text[], coalesce(v_existing_version, 0) + 1,
      (p_quarter->>'source_updated_at')::timestamptz, now(), now()
    ) on conflict (company_id, fiscal_year, fiscal_quarter) do update set
      market_year = excluded.market_year, market_quarter = excluded.market_quarter,
      period_start = excluded.period_start, period_end = excluded.period_end,
      revenue = excluded.revenue, operating_income = excluded.operating_income,
      net_income = excluded.net_income, eps = excluded.eps,
      currency = excluded.currency, consolidation_scope = excluded.consolidation_scope,
      source_filing_id = excluded.source_filing_id, quality_status = 'complete',
      missing_metrics = '{}'::text[],
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

revoke all on function public.complete_earnings_open_dart_job(bigint, uuid, jsonb, jsonb, jsonb, text)
  from public, anon, authenticated;
grant execute on function public.complete_earnings_open_dart_job(bigint, uuid, jsonb, jsonb, jsonb, text)
  to service_role;

create or replace function public.fail_earnings_open_dart_job(
  p_job_id bigint,
  p_error text,
  p_retry_delay_seconds integer default 300
)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_status text;
begin
  if p_retry_delay_seconds < 0 or p_retry_delay_seconds > 86400 then
    raise exception 'Invalid OpenDART retry delay';
  end if;
  update public.earnings_ingestion_jobs
  set status = case when attempts >= max_attempts then 'failed' else 'retry' end,
      available_at = case when attempts >= max_attempts then available_at
                          else now() + make_interval(secs => p_retry_delay_seconds) end,
      completed_at = case when attempts >= max_attempts then now() else null end,
      claimed_at = null,
      last_error = left(coalesce(p_error, 'OpenDART worker failed'), 1000),
      updated_at = now()
  where id = p_job_id and source = 'open_dart' and status = 'running'
  returning status into v_status;
  return v_status;
end;
$$;

revoke all on function public.fail_earnings_open_dart_job(bigint, text, integer)
  from public, anon, authenticated;
grant execute on function public.fail_earnings_open_dart_job(bigint, text, integer)
  to service_role;
