-- OpenDART structured financial statements are available from business year
-- 2015. This boundary is material: 2015 is the YoY base for 2016, and legacy
-- archive reconstruction can select a non-comparable top-line account.

create or replace function public.enqueue_earnings_open_dart_legacy_backfill()
returns integer language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_inserted integer;
begin
  insert into public.earnings_ingestion_jobs
    (source, job_kind, company_id, business_year, report_code, reason,
     priority, metadata, collector_variant)
  select 'open_dart', 'financial_period', companies.company_id,
    years.business_year, reports.report_code, 'new_company', 10,
    jsonb_build_object('backfill', true, 'legacy_archive', true),
    'legacy_archive'
  from (
    select distinct memberships.company_id
    from public.earnings_index_memberships memberships
    join public.earnings_indices indices on indices.index_id = memberships.index_id
    where memberships.effective_to is null and indices.country = 'KR'
  ) companies
  cross join lateral generate_series(2002, 2014) years(business_year)
  cross join (values ('11013'), ('11012'), ('11014'), ('11011')) reports(report_code)
  where exists (
    select 1 from public.earnings_company_identifiers identifiers
    where identifiers.company_id = companies.company_id
      and identifiers.identifier_type = 'dart_corp_code'
      and identifiers.valid_to is null
  )
  and not exists (
    select 1 from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.company_id = companies.company_id
      and jobs.business_year = years.business_year
      and jobs.report_code = reports.report_code
      and jobs.collector_variant = 'legacy_archive'
  )
  and not exists (
    select 1 from public.earnings_quarterly_financials financials
    where financials.company_id = companies.company_id
      and financials.fiscal_year = years.business_year
      and financials.fiscal_quarter = case reports.report_code
        when '11013' then 1 when '11012' then 2
        when '11014' then 3 when '11011' then 4 end
  );
  get diagnostics v_inserted = row_count;
  return v_inserted;
end;
$$;
revoke all on function public.enqueue_earnings_open_dart_legacy_backfill()
  from public, anon, authenticated;
grant execute on function public.enqueue_earnings_open_dart_legacy_backfill()
  to service_role;

create or replace function public.claim_earnings_open_dart_jobs(p_limit integer default 100)
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_year integer; v_report_code text; v_result jsonb;
begin
  if p_limit < 1 or p_limit > 100 then
    raise exception 'OpenDART claim limit must be between 1 and 100';
  end if;
  update public.earnings_ingestion_jobs
  set status = 'retry', claimed_at = null, available_at = now(), updated_at = now(),
      last_error = 'Worker lease expired'
  where source = 'open_dart' and status = 'running'
    and collector_variant = 'structured'
    and claimed_at < now() - interval '30 minutes';
  select jobs.business_year, jobs.report_code into v_year, v_report_code
  from public.earnings_ingestion_jobs jobs
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.collector_variant = 'structured' and jobs.business_year >= 2015
    and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
    and jobs.attempts < jobs.max_attempts
    and exists (
      select 1 from public.earnings_company_identifiers identifiers
      where identifiers.company_id = jobs.company_id
        and identifiers.identifier_type = 'dart_corp_code'
        and identifiers.valid_to is null
    )
  order by jobs.priority desc, jobs.available_at asc, jobs.id asc
  for update skip locked limit 1;
  if v_year is null then return '[]'::jsonb; end if;
  with candidates as (
    select jobs.id from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.collector_variant = 'structured'
      and jobs.business_year = v_year and jobs.report_code = v_report_code
      and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
      and jobs.attempts < jobs.max_attempts
    order by jobs.priority desc, jobs.available_at asc, jobs.id asc
    for update skip locked limit p_limit
  ), claimed as (
    update public.earnings_ingestion_jobs jobs
    set status = 'running', attempts = attempts + 1, claimed_at = now(),
        last_error = null, updated_at = now()
    from candidates where jobs.id = candidates.id returning jobs.*
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', claimed.id, 'company_id', claimed.company_id,
    'business_year', claimed.business_year, 'report_code', claimed.report_code,
    'reason', claimed.reason, 'attempts', claimed.attempts,
    'metadata', claimed.metadata, 'corp_code', identifiers.identifier_value,
    'ticker', companies.ticker, 'company_name', companies.company_name
  ) order by claimed.priority desc, claimed.id asc), '[]'::jsonb) into v_result
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

create or replace function public.claim_earnings_open_dart_legacy_jobs()
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_company_id uuid; v_year integer; v_result jsonb;
begin
  update public.earnings_ingestion_jobs
  set status = 'retry', claimed_at = null, available_at = now(), updated_at = now(),
      last_error = 'Worker lease expired'
  where source = 'open_dart' and status = 'running'
    and collector_variant = 'legacy_archive'
    and business_year between 2002 and 2014
    and claimed_at < now() - interval '30 minutes';

  select jobs.company_id, jobs.business_year into v_company_id, v_year
  from public.earnings_ingestion_jobs jobs
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.collector_variant = 'legacy_archive'
    and jobs.business_year between 2002 and 2014
    and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
    and jobs.attempts < jobs.max_attempts
    and coalesce(jobs.metadata->>'receipt_no', '') <> ''
    and not exists (
      select 1 from public.earnings_ingestion_jobs running
      where running.source = jobs.source and running.job_kind = jobs.job_kind
        and running.company_id = jobs.company_id and running.status = 'running'
    )
  order by jobs.business_year asc, jobs.company_id asc, jobs.id asc
  for update skip locked limit 1;
  if v_company_id is null then return '[]'::jsonb; end if;

  with candidates as (
    select jobs.id from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.company_id = v_company_id and jobs.business_year = v_year
      and jobs.collector_variant = 'legacy_archive'
      and jobs.business_year between 2002 and 2014
      and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
      and jobs.attempts < jobs.max_attempts
      and coalesce(jobs.metadata->>'receipt_no', '') <> ''
    order by jobs.id for update skip locked
  ), claimed as (
    update public.earnings_ingestion_jobs jobs
    set status = 'running', attempts = attempts + 1, claimed_at = now(),
        last_error = null, updated_at = now()
    from candidates where jobs.id = candidates.id returning jobs.*
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', claimed.id, 'company_id', claimed.company_id,
    'business_year', claimed.business_year, 'report_code', claimed.report_code,
    'reason', claimed.reason, 'attempts', claimed.attempts,
    'metadata', claimed.metadata, 'corp_code', identifiers.identifier_value,
    'ticker', companies.ticker, 'company_name', companies.company_name
  ) order by claimed.id), '[]'::jsonb) into v_result
  from claimed
  join public.earnings_companies companies on companies.id = claimed.company_id
  join public.earnings_company_identifiers identifiers
    on identifiers.company_id = claimed.company_id
   and identifiers.identifier_type = 'dart_corp_code'
   and identifiers.valid_to is null;
  return v_result;
end;
$$;
revoke all on function public.claim_earnings_open_dart_legacy_jobs()
  from public, anon, authenticated;
grant execute on function public.claim_earnings_open_dart_legacy_jobs()
  to service_role;

create or replace function public.requeue_unvalidated_legacy_earnings_jobs()
returns integer language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_existing integer; v_requeued integer;
begin
  update public.earnings_ingestion_jobs outstanding
  set status='retry', attempts=0, available_at=now(), claimed_at=null,
      last_error=null,
      metadata=outstanding.metadata || jsonb_build_object(
        'quality_revalidation', true,
        'quality_revalidation_version', 1
      ),
      updated_at=now()
  where outstanding.collector_variant='legacy_archive'
    and outstanding.business_year between 2002 and 2014
    and outstanding.status in ('pending', 'retry')
    and exists (
      select 1 from public.earnings_ingestion_jobs completed
      where completed.collector_variant='legacy_archive'
        and completed.status='completed'
        and completed.business_year between 2002 and 2014
        and completed.source=outstanding.source
        and completed.job_kind=outstanding.job_kind
        and completed.company_id=outstanding.company_id
        and completed.business_year=outstanding.business_year
        and coalesce(completed.report_code,'')=coalesce(outstanding.report_code,'')
        and completed.metadata->>'receipt_no'=outstanding.metadata->>'receipt_no'
        and coalesce(completed.metadata->>'receipt_no','')<>''
        and not exists (
          select 1 from public.earnings_filings filings
          where filings.source='open_dart'
            and filings.source_filing_id=completed.metadata->>'receipt_no'
            and filings.metadata ? 'quality_issues'
        )
    );
  get diagnostics v_existing = row_count;

  update public.earnings_ingestion_jobs jobs
  set status='retry', attempts=0, available_at=now(), claimed_at=null,
      completed_at=null, last_error=null,
      metadata=jobs.metadata || jsonb_build_object(
        'quality_revalidation', true,
        'quality_revalidation_version', 1
      ),
      updated_at=now()
  where jobs.collector_variant='legacy_archive'
    and jobs.status='completed'
    and jobs.business_year between 2002 and 2014
    and coalesce(jobs.metadata->>'receipt_no','')<>''
    and jobs.id = (
      select min(candidate.id)
      from public.earnings_ingestion_jobs candidate
      where candidate.collector_variant='legacy_archive'
        and candidate.status='completed'
        and candidate.source=jobs.source
        and candidate.job_kind=jobs.job_kind
        and candidate.company_id=jobs.company_id
        and candidate.business_year=jobs.business_year
        and coalesce(candidate.report_code,'')=coalesce(jobs.report_code,'')
        and coalesce(candidate.metadata->>'receipt_no','')<>''
        and not exists (
          select 1 from public.earnings_filings candidate_filing
          where candidate_filing.source='open_dart'
            and candidate_filing.source_filing_id=candidate.metadata->>'receipt_no'
            and candidate_filing.metadata ? 'quality_issues'
        )
    )
    and not exists (
      select 1 from public.earnings_ingestion_jobs outstanding
      where outstanding.source=jobs.source
        and outstanding.job_kind=jobs.job_kind
        and outstanding.company_id=jobs.company_id
        and outstanding.business_year=jobs.business_year
        and coalesce(outstanding.report_code,'')=coalesce(jobs.report_code,'')
        and outstanding.status in ('pending', 'running', 'retry')
    )
    and not exists (
      select 1 from public.earnings_filings filings
      where filings.source='open_dart'
        and filings.source_filing_id=jobs.metadata->>'receipt_no'
        and filings.metadata ? 'quality_issues'
    );
  get diagnostics v_requeued = row_count;
  return v_existing + v_requeued;
end;
$$;
revoke all on function public.requeue_unvalidated_legacy_earnings_jobs()
  from public, anon, authenticated;
grant execute on function public.requeue_unvalidated_legacy_earnings_jobs()
  to service_role;

-- One versioned repair queue is replay-safe. A future parser revision can use
-- a new version without reopening this historical repair indefinitely.
create or replace function public.enqueue_earnings_structured_2015_repair()
returns integer language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_reused integer; v_inserted integer;
begin
  with targets as (
    select distinct companies.id as company_id
    from public.earnings_companies companies
    where companies.country = 'KR'
      and exists (
        select 1 from public.earnings_company_identifiers identifiers
        where identifiers.company_id = companies.id
          and identifiers.identifier_type = 'dart_corp_code'
          and identifiers.valid_to is null
      )
      and (
        exists (
          select 1 from public.earnings_quarterly_financials financials
          where financials.company_id = companies.id
            and financials.fiscal_year = 2015
        )
        or exists (
          select 1 from public.earnings_index_memberships memberships
          where memberships.company_id = companies.id
            and memberships.effective_to is null
        )
      )
  )
  update public.earnings_ingestion_jobs jobs
  set collector_variant = 'structured', reason = 'repair', priority = 95,
      status = 'retry', attempts = 0, available_at = now(), claimed_at = null,
      completed_at = null, last_error = null,
      metadata = jobs.metadata || jsonb_build_object(
        'structured_2015_repair_version', 1,
        'repair_reason', 'replace_legacy_2015_yoy_baseline'
      ), updated_at = now()
  from targets
  where jobs.company_id = targets.company_id
    and jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.business_year = 2015
    and jobs.status in ('pending', 'running', 'retry');
  get diagnostics v_reused = row_count;

  with targets as (
    select distinct companies.id as company_id
    from public.earnings_companies companies
    where companies.country = 'KR'
      and exists (
        select 1 from public.earnings_company_identifiers identifiers
        where identifiers.company_id = companies.id
          and identifiers.identifier_type = 'dart_corp_code'
          and identifiers.valid_to is null
      )
      and (
        exists (
          select 1 from public.earnings_quarterly_financials financials
          where financials.company_id = companies.id
            and financials.fiscal_year = 2015
        )
        or exists (
          select 1 from public.earnings_index_memberships memberships
          where memberships.company_id = companies.id
            and memberships.effective_to is null
        )
      )
  )
  insert into public.earnings_ingestion_jobs (
    source, job_kind, company_id, business_year, report_code,
    reason, priority, metadata, collector_variant
  )
  select 'open_dart', 'financial_period', targets.company_id, 2015,
    reports.report_code, 'repair', 95,
    jsonb_build_object(
      'structured_2015_repair_version', 1,
      'repair_reason', 'replace_legacy_2015_yoy_baseline'
    ), 'structured'
  from targets
  cross join (values ('11013'), ('11012'), ('11014'), ('11011')) reports(report_code)
  where not exists (
    select 1 from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.company_id = targets.company_id and jobs.business_year = 2015
      and jobs.report_code = reports.report_code
      and (
        jobs.status in ('pending', 'running', 'retry')
        or jobs.metadata @> '{"structured_2015_repair_version": 1}'::jsonb
      )
  );
  get diagnostics v_inserted = row_count;
  return v_reused + v_inserted;
end;
$$;
revoke all on function public.enqueue_earnings_structured_2015_repair()
  from public, anon, authenticated;
grant execute on function public.enqueue_earnings_structured_2015_repair()
  to service_role;

comment on function public.enqueue_earnings_structured_2015_repair() is
  'Idempotently replaces legacy 2015 canonical quarters through structured OpenDART before recalculating 2016 YoY metrics.';
