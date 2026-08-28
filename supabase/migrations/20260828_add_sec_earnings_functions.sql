-- Compact SEC ingestion. One public company-facts response can contain the
-- complete ten-year window, so the worker upserts all changed quarters for a
-- company atomically without storing the provider response.

create or replace function public.list_current_sec_earnings_companies()
returns table(company_id uuid, cik text, ticker text)
language sql
security definer
set search_path = public, pg_temp
as $$
  select distinct companies.id, identifiers.identifier_value, companies.ticker
  from public.earnings_companies companies
  join public.earnings_index_memberships memberships
    on memberships.company_id = companies.id and memberships.effective_to is null
  join public.earnings_indices indices
    on indices.index_id = memberships.index_id and indices.country = 'US'
  join public.earnings_company_identifiers identifiers
    on identifiers.company_id = companies.id
   and identifiers.identifier_type = 'sec_cik'
   and identifiers.valid_to is null
  where companies.country = 'US'
  order by companies.id;
$$;

revoke all on function public.list_current_sec_earnings_companies()
  from public, anon, authenticated;
grant execute on function public.list_current_sec_earnings_companies()
  to service_role;

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
       or nullif(v_quarter->>'revenue', '') is null
       or nullif(v_quarter->>'operating_income', '') is null
       or nullif(v_quarter->>'net_income', '') is null then
      raise exception 'Incomplete SEC quarter row';
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
      period_start, period_end, revenue, operating_income, net_income,
      currency, consolidation_scope, source_filing_id, quality_status,
      missing_metrics, canonical_version, source_updated_at, calculated_at, updated_at
    ) values (
      p_company_id, (v_quarter->>'fiscal_year')::integer,
      (v_quarter->>'fiscal_quarter')::smallint,
      (v_quarter->>'market_year')::integer, (v_quarter->>'market_quarter')::smallint,
      (v_quarter->>'period_start')::date, (v_quarter->>'period_end')::date,
      (v_quarter->>'revenue')::numeric, (v_quarter->>'operating_income')::numeric,
      (v_quarter->>'net_income')::numeric,
      'USD', 'NA', v_filing_id, 'complete', '{}'::text[],
      coalesce(v_existing_version, 0) + 1,
      (v_quarter->>'source_updated_at')::timestamptz, now(), now()
    ) on conflict (company_id, fiscal_year, fiscal_quarter) do update set
      market_year = excluded.market_year,
      market_quarter = excluded.market_quarter,
      period_start = excluded.period_start,
      period_end = excluded.period_end,
      revenue = excluded.revenue,
      operating_income = excluded.operating_income,
      net_income = excluded.net_income,
      currency = 'USD',
      consolidation_scope = 'NA',
      source_filing_id = excluded.source_filing_id,
      quality_status = 'complete',
      missing_metrics = '{}'::text[],
      canonical_version = public.earnings_quarterly_financials.canonical_version + 1,
      source_updated_at = excluded.source_updated_at,
      calculated_at = now(),
      updated_at = now()
    where public.earnings_quarterly_financials.source_filing_id is distinct from excluded.source_filing_id
       or public.earnings_quarterly_financials.revenue is distinct from excluded.revenue
       or public.earnings_quarterly_financials.operating_income is distinct from excluded.operating_income
       or public.earnings_quarterly_financials.net_income is distinct from excluded.net_income;
    if found then v_changed := v_changed + 1; end if;
    v_seen := v_seen + 1;
  end loop;

  return jsonb_build_object('company_id', p_company_id, 'seen', v_seen, 'changed', v_changed);
end;
$$;

revoke all on function public.upsert_sec_company_quarters(uuid, jsonb)
  from public, anon, authenticated;
grant execute on function public.upsert_sec_company_quarters(uuid, jsonb)
  to service_role;
