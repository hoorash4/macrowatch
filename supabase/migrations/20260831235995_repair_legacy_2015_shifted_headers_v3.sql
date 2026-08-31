-- Re-run 2015 with parser v3. V2 still consulted per-column context before
-- recognizing paired interim columns, so malformed DART colspans could keep
-- mapping a three-month value to the cumulative period.
create or replace function public.enqueue_earnings_legacy_2015_parser_v3_repair()
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
  set collector_variant = 'legacy_archive', reason = 'repair', priority = 10,
      status = 'retry', attempts = 0, available_at = now(), claimed_at = null,
      completed_at = null, last_error = null,
      metadata = (jobs.metadata - 'outcome') || jsonb_build_object(
        'legacy_2015_parser_version', 3,
        'legacy_archive', true,
        'repair_reason', 'correct_shifted_interim_header_context_order'
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
    reports.report_code, 'repair', 10,
    jsonb_build_object(
      'legacy_2015_parser_version', 3,
      'legacy_archive', true,
      'repair_reason', 'correct_shifted_interim_header_context_order'
    ), 'legacy_archive'
  from targets
  cross join (values ('11013'), ('11012'), ('11014'), ('11011')) reports(report_code)
  where not exists (
    select 1 from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.company_id = targets.company_id and jobs.business_year = 2015
      and jobs.report_code = reports.report_code
      and jobs.metadata @> '{"legacy_2015_parser_version": 3}'::jsonb
  );
  get diagnostics v_inserted = row_count;
  return v_reused + v_inserted;
end;
$$;

revoke all on function public.enqueue_earnings_legacy_2015_parser_v3_repair()
  from public, anon, authenticated;
grant execute on function public.enqueue_earnings_legacy_2015_parser_v3_repair()
  to service_role;

comment on function public.enqueue_earnings_legacy_2015_parser_v3_repair() is
  'Idempotently rebuilds 2015 after prioritizing paired interim layouts over shifted per-column headers.';
