-- Keep only the compact records MacroWatch uses: filing identity and one
-- canonical row per company/quarter. Provider responses are parsed in worker
-- memory and are intentionally not persisted.

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
    if cardinality(v_missing) <> 0
       or nullif(p_quarter->>'revenue', '') is null
       or nullif(p_quarter->>'operating_income', '') is null
       or nullif(p_quarter->>'net_income', '') is null then
      raise exception 'Incomplete core metrics cannot replace canonical financials';
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
      (p_quarter->>'net_income')::numeric, nullif(p_quarter->>'eps', '')::numeric,
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

revoke all on function public.complete_earnings_open_dart_job(bigint, jsonb, jsonb, text)
  from public, anon, authenticated;
grant execute on function public.complete_earnings_open_dart_job(bigint, jsonb, jsonb, text)
  to service_role;

-- Retire the audit-heavy persistence API before dropping its backing tables.
drop function if exists public.complete_earnings_open_dart_job(bigint, uuid, jsonb, jsonb, jsonb, text);
drop function if exists public.save_earnings_open_dart_payload(text, text, jsonb, text, jsonb);

drop table if exists public.earnings_financial_facts;
alter table public.earnings_filings drop column if exists source_payload_id;
drop table if exists public.earnings_source_payloads;

comment on table public.earnings_quarterly_financials is
  'Compact canonical quarterly revenue, operating income, net income, and optional EPS used by Earnings Momentum.';
