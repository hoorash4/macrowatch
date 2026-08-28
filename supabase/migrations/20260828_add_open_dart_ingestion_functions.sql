-- Atomic OpenDART identity and job-queue operations.
-- Provider workers never write canonical financial rows directly while a
-- batch is only partially processed.

create or replace function public.sync_earnings_open_dart_identifiers(
  p_identifiers jsonb,
  p_valid_from date
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_row jsonb;
  v_company_id uuid;
  v_ticker text;
  v_corp_code text;
  v_matched integer := 0;
  v_unresolved text[] := '{}';
begin
  if jsonb_typeof(p_identifiers) <> 'array' then
    raise exception 'OpenDART identifiers must be a JSON array';
  end if;

  for v_row in select value from jsonb_array_elements(p_identifiers)
  loop
    v_ticker := trim(coalesce(v_row->>'ticker', ''));
    v_corp_code := trim(coalesce(v_row->>'corp_code', ''));
    if v_ticker !~ '^\d{6}$' or v_corp_code !~ '^\d{8}$' then
      raise exception 'Invalid OpenDART identifier row';
    end if;

    select id into v_company_id
    from public.earnings_companies
    where country = 'KR' and ticker = v_ticker
    order by created_at asc
    limit 1;

    if v_company_id is null then
      v_unresolved := array_append(v_unresolved, v_ticker);
      continue;
    end if;

    insert into public.earnings_company_identifiers
      (company_id, identifier_type, identifier_value, is_primary, valid_from, valid_to, updated_at)
    values
      (v_company_id, 'dart_corp_code', v_corp_code, true, p_valid_from, null, now())
    on conflict (company_id, identifier_type, identifier_value)
    do update set is_primary = true, valid_to = null, updated_at = now();
    v_matched := v_matched + 1;
  end loop;

  return jsonb_build_object(
    'matched', v_matched,
    'unresolved_count', cardinality(v_unresolved),
    'unresolved_tickers', to_jsonb(v_unresolved)
  );
end;
$$;

revoke all on function public.sync_earnings_open_dart_identifiers(jsonb, date)
  from public, anon, authenticated;
grant execute on function public.sync_earnings_open_dart_identifiers(jsonb, date)
  to service_role;

create or replace function public.enqueue_earnings_open_dart_backfill(
  p_as_of_year integer,
  p_years integer default 5
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

  insert into public.earnings_ingestion_jobs
    (source, job_kind, company_id, business_year, report_code, reason, priority, metadata)
  select
    'open_dart', 'financial_period', companies.company_id, years.business_year,
    reports.report_code, 'new_company', 20,
    jsonb_build_object('backfill', true)
  from (
    select distinct memberships.company_id
    from public.earnings_index_memberships memberships
    join public.earnings_indices indices on indices.index_id = memberships.index_id
    where memberships.effective_to is null and indices.country = 'KR'
  ) companies
  cross join lateral generate_series(p_as_of_year - p_years + 1, p_as_of_year) years(business_year)
  cross join (values ('11013'), ('11012'), ('11014'), ('11011')) reports(report_code)
  where exists (
    select 1 from public.earnings_company_identifiers identifiers
    where identifiers.company_id = companies.company_id
      and identifiers.identifier_type = 'dart_corp_code'
      and identifiers.valid_to is null
  )
  and not exists (
    select 1 from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart'
      and jobs.job_kind = 'financial_period'
      and jobs.company_id = companies.company_id
      and jobs.business_year = years.business_year
      and jobs.report_code = reports.report_code
  );
  get diagnostics v_inserted = row_count;
  return v_inserted;
end;
$$;

revoke all on function public.enqueue_earnings_open_dart_backfill(integer, integer)
  from public, anon, authenticated;
grant execute on function public.enqueue_earnings_open_dart_backfill(integer, integer)
  to service_role;

create or replace function public.enqueue_earnings_open_dart_filings(p_filings jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_row jsonb;
  v_company_id uuid;
  v_job_id bigint;
  v_corp_code text;
  v_receipt_no text;
  v_year integer;
  v_report_code text;
  v_metadata jsonb;
  v_queued integer := 0;
  v_untracked integer := 0;
begin
  if jsonb_typeof(p_filings) <> 'array' then
    raise exception 'OpenDART filings must be a JSON array';
  end if;

  for v_row in select value from jsonb_array_elements(p_filings)
  loop
    v_corp_code := trim(coalesce(v_row->>'corp_code', ''));
    v_receipt_no := trim(coalesce(v_row->>'receipt_no', ''));
    v_year := nullif(v_row->>'business_year', '')::integer;
    v_report_code := trim(coalesce(v_row->>'report_code', ''));
    if v_corp_code !~ '^\d{8}$' or v_receipt_no !~ '^\d{14}$'
       or v_year is null or v_report_code not in ('11013', '11012', '11014', '11011') then
      raise exception 'Invalid OpenDART filing row';
    end if;

    select company_id into v_company_id
    from public.earnings_company_identifiers
    where identifier_type = 'dart_corp_code'
      and identifier_value = v_corp_code
      and valid_to is null
    limit 1;
    if v_company_id is null then
      v_untracked := v_untracked + 1;
      continue;
    end if;

    v_metadata := jsonb_strip_nulls(jsonb_build_object(
      'receipt_no', v_receipt_no,
      'filed_on', v_row->>'filed_on',
      'report_name', v_row->>'report_name',
      'is_correction', coalesce((v_row->>'is_correction')::boolean, false)
    ));

    select id into v_job_id
    from public.earnings_ingestion_jobs
    where source = 'open_dart' and job_kind = 'financial_period'
      and company_id = v_company_id and business_year = v_year
      and report_code = v_report_code and status in ('pending', 'running', 'retry')
    order by priority desc, id asc
    limit 1;

    if v_job_id is not null then
      update public.earnings_ingestion_jobs
      set priority = 100, reason = 'live_filing', metadata = v_metadata, updated_at = now()
      where id = v_job_id;
    elsif not exists (
      select 1 from public.earnings_ingestion_jobs
      where source = 'open_dart' and job_kind = 'financial_period'
        and company_id = v_company_id and business_year = v_year
        and report_code = v_report_code
        and metadata->>'receipt_no' = v_receipt_no
    ) then
      insert into public.earnings_ingestion_jobs
        (source, job_kind, company_id, business_year, report_code, reason, priority, metadata)
      values
        ('open_dart', 'financial_period', v_company_id, v_year, v_report_code,
         'live_filing', 100, v_metadata);
      v_queued := v_queued + 1;
    end if;
  end loop;

  return jsonb_build_object('queued', v_queued, 'untracked', v_untracked);
end;
$$;

revoke all on function public.enqueue_earnings_open_dart_filings(jsonb)
  from public, anon, authenticated;
grant execute on function public.enqueue_earnings_open_dart_filings(jsonb)
  to service_role;

create or replace function public.save_earnings_open_dart_payload(
  p_operation text,
  p_request_key text,
  p_request_params jsonb,
  p_payload_sha256 text,
  p_response_payload jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_id uuid;
begin
  if trim(p_operation) = '' or trim(p_request_key) = ''
     or p_payload_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'Invalid OpenDART source payload metadata';
  end if;

  insert into public.earnings_source_payloads
    (source, operation, request_key, request_params, response_payload,
     payload_sha256, status, completed_at)
  values
    ('open_dart', p_operation, p_request_key, coalesce(p_request_params, '{}'::jsonb),
     p_response_payload, p_payload_sha256, 'completed', now())
  on conflict (source, operation, request_key, payload_sha256)
    where payload_sha256 is not null
  do update set response_payload = excluded.response_payload,
                status = 'completed', completed_at = now(), error_message = null
  returning id into v_id;
  return v_id;
end;
$$;

revoke all on function public.save_earnings_open_dart_payload(text, text, jsonb, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.save_earnings_open_dart_payload(text, text, jsonb, text, jsonb)
  to service_role;
