-- Preserve valid partial OpenDART quarters so a missing top line can be
-- repaired independently, then enqueue every currently tracked Korean row
-- whose revenue is still absent. The worker only promotes these rows to
-- complete after its DART statement reconciliation succeeds.

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
    if not v_missing <@ array['revenue', 'operating_income', 'net_income']::text[] then
      raise exception 'Unknown core metric in missing_metrics';
    end if;
    if nullif(p_quarter->>'revenue', '') is null
       and nullif(p_quarter->>'operating_income', '') is null
       and nullif(p_quarter->>'net_income', '') is null then
      raise exception 'At least one canonical core metric is required';
    end if;
    v_quality := case when cardinality(v_missing) = 0 then 'complete' else 'partial' end;

    select canonical_version into v_existing_version
    from public.earnings_quarterly_financials
    where company_id = v_job.company_id
      and fiscal_year = v_job.business_year
      and fiscal_quarter = (p_quarter->>'fiscal_quarter')::smallint;

    insert into public.earnings_quarterly_financials (
      company_id, fiscal_year, fiscal_quarter, market_year, market_quarter,
      period_start, period_end, revenue, operating_income, net_income,
      currency, consolidation_scope, source_filing_id, quality_status,
      missing_metrics, canonical_version, source_updated_at, calculated_at, updated_at
    ) values (
      v_job.company_id, v_job.business_year, (p_quarter->>'fiscal_quarter')::smallint,
      (p_quarter->>'market_year')::integer, (p_quarter->>'market_quarter')::smallint,
      nullif(p_quarter->>'period_start', '')::date, (p_quarter->>'period_end')::date,
      nullif(p_quarter->>'revenue', '')::numeric,
      nullif(p_quarter->>'operating_income', '')::numeric,
      nullif(p_quarter->>'net_income', '')::numeric,
      p_quarter->>'currency', p_quarter->>'consolidation_scope', v_filing_id,
      v_quality, v_missing, coalesce(v_existing_version, 0) + 1,
      (p_quarter->>'source_updated_at')::timestamptz, now(), now()
    ) on conflict (company_id, fiscal_year, fiscal_quarter) do update set
      market_year = excluded.market_year, market_quarter = excluded.market_quarter,
      period_start = excluded.period_start, period_end = excluded.period_end,
      revenue = excluded.revenue, operating_income = excluded.operating_income,
      net_income = excluded.net_income,
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

revoke all on function public.complete_earnings_open_dart_job(bigint, jsonb, jsonb, text)
  from public, anon, authenticated;
grant execute on function public.complete_earnings_open_dart_job(bigint, jsonb, jsonb, text)
  to service_role;

insert into public.earnings_ingestion_jobs (
  source, job_kind, company_id, business_year, report_code,
  reason, priority, metadata
)
select
  'open_dart', 'financial_period', financials.company_id, financials.fiscal_year,
  case financials.fiscal_quarter
    when 1 then '11013' when 2 then '11012'
    when 3 then '11014' when 4 then '11011'
  end,
  'repair', 90,
  jsonb_build_object(
    'receipt_no', filings.source_filing_id,
    'filed_on', filings.filing_date,
    'report_name', filings.metadata->>'report_name',
    'is_correction', filings.is_correction,
    'revenue_repair', true
  )
from public.earnings_quarterly_financials financials
join public.earnings_companies companies on companies.id = financials.company_id
join public.earnings_filings filings on filings.id = financials.source_filing_id
where companies.country = 'KR'
  and financials.revenue is null
  and (financials.operating_income is not null or financials.net_income is not null)
  and exists (
    select 1
    from public.earnings_index_memberships memberships
    join public.earnings_indices indices on indices.index_id = memberships.index_id
    where memberships.company_id = financials.company_id
      and memberships.effective_to is null
      and indices.country = 'KR'
  )
  and exists (
    select 1 from public.earnings_company_identifiers identifiers
    where identifiers.company_id = financials.company_id
      and identifiers.identifier_type = 'dart_corp_code'
      and identifiers.valid_to is null
  )
  and not exists (
    select 1 from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart'
      and jobs.job_kind = 'financial_period'
      and jobs.company_id = financials.company_id
      and jobs.business_year = financials.fiscal_year
      and coalesce(jobs.report_code, '') = case financials.fiscal_quarter
        when 1 then '11013' when 2 then '11012'
        when 3 then '11014' when 4 then '11011'
      end
      and jobs.reason = 'repair'
      and coalesce((jobs.metadata->>'revenue_repair')::boolean, false)
  );

-- Replaying this migration is safe: the metadata predicate above preserves
-- the first repair job for each company-period instead of enqueueing duplicates.
