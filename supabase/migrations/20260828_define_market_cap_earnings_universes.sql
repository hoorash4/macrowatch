-- Earnings Momentum universes are market-cap selections, not official indices.
-- Keep the original IDs for application compatibility while correcting their
-- display names, sources, and effective-dated snapshot model.

update public.earnings_indices
set index_name = case index_id
      when 'SP100' then 'S&P 500 시가총액 상위 100'
      when 'NASDAQ100' then 'NASDAQ 시가총액 상위 100'
      when 'KOSPI100' then 'KOSPI 시가총액 상위 100'
      when 'KOSDAQ50' then 'KOSDAQ 시가총액 상위 50'
      else index_name
    end,
    constituent_source = case index_id
      when 'SP100' then 'S&P 500 constituents + KIS market cap'
      when 'NASDAQ100' then 'KIS overseas market-cap ranking'
      when 'KOSPI100' then 'KIS domestic market-cap ranking'
      when 'KOSDAQ50' then 'KIS domestic market-cap ranking'
      else constituent_source
    end,
    updated_at = now()
where index_id in ('SP100', 'NASDAQ100', 'KOSPI100', 'KOSDAQ50');

create table if not exists public.earnings_universe_snapshots (
  index_id text not null references public.earnings_indices(index_id) on delete restrict,
  observed_on date not null,
  company_id uuid not null references public.earnings_companies(id) on delete restrict,
  rank smallint not null check (rank > 0),
  market_cap numeric(38, 4) not null check (market_cap >= 0),
  market_cap_currency text not null check (market_cap_currency ~ '^[A-Z]{3}$'),
  source text not null,
  source_reference text,
  created_at timestamptz not null default now(),
  primary key (index_id, observed_on, company_id),
  unique (index_id, observed_on, rank)
);

create index if not exists earnings_universe_snapshots_company_idx
  on public.earnings_universe_snapshots (company_id, observed_on desc);

alter table public.earnings_universe_snapshots enable row level security;

drop policy if exists "Authenticated users can read earnings universe snapshots"
  on public.earnings_universe_snapshots;
create policy "Authenticated users can read earnings universe snapshots"
  on public.earnings_universe_snapshots for select to authenticated using (true);

comment on table public.earnings_universe_snapshots is
  'Point-in-time market-cap ranks used to derive effective-dated Earnings Momentum universe membership.';

-- Apply one complete market-cap snapshot atomically. Provider collection and
-- parsing remain outside the database; membership transitions happen here so
-- a partial Edge Function failure cannot leave half of a universe replaced.
create or replace function public.sync_earnings_market_cap_universe(
  p_index_id text,
  p_observed_on date,
  p_constituents jsonb,
  p_source text,
  p_source_reference text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_country text;
  v_target_count integer;
  v_identifier_type text;
  v_currency text;
  v_item jsonb;
  v_company_id uuid;
  v_ticker text;
  v_name text;
  v_rank integer;
  v_market_cap numeric;
  v_added integer := 0;
  v_removed integer := 0;
  v_removed_same_day integer := 0;
  v_snapshot_count integer;
begin
  select country, target_count
    into v_country, v_target_count
  from public.earnings_indices
  where index_id = p_index_id and is_active;

  if not found then
    raise exception 'Unknown or inactive earnings universe: %', p_index_id;
  end if;
  if jsonb_typeof(p_constituents) <> 'array'
     or jsonb_array_length(p_constituents) <> v_target_count then
    raise exception 'Universe % requires exactly % constituents', p_index_id, v_target_count;
  end if;

  v_identifier_type := case when v_country = 'KR' then 'krx_ticker' else 'us_ticker' end;
  v_currency := case when v_country = 'KR' then 'KRW' else 'USD' end;

  create temporary table if not exists pg_temp.incoming_earnings_universe (
    company_id uuid primary key,
    rank integer not null unique
  ) on commit drop;
  truncate pg_temp.incoming_earnings_universe;

  -- The function receives a complete replacement snapshot. Deleting the same
  -- day's old rows first also allows two companies to swap ranks on a rerun
  -- without tripping the unique rank constraint halfway through the loop.
  delete from public.earnings_universe_snapshots
  where index_id = p_index_id and observed_on = p_observed_on;

  for v_item in select value from jsonb_array_elements(p_constituents)
  loop
    v_ticker := upper(trim(v_item->>'ticker'));
    v_name := trim(v_item->>'name');
    v_rank := (v_item->>'rank')::integer;
    v_market_cap := (v_item->>'market_cap')::numeric;
    if v_ticker = '' or v_name = '' or v_rank < 1 or v_market_cap < 0 then
      raise exception 'Invalid constituent row in universe %', p_index_id;
    end if;

    select company_id into v_company_id
    from public.earnings_company_identifiers
    where identifier_type = v_identifier_type and identifier_value = v_ticker;

    if v_company_id is null then
      insert into public.earnings_companies
        (country, company_name, ticker, exchange, reporting_currency)
      values
        (v_country, v_name, v_ticker,
         case
           when p_index_id = 'KOSPI100' then 'KOSPI'
           when p_index_id = 'KOSDAQ50' then 'KOSDAQ'
           when p_index_id = 'NASDAQ100' then 'NASDAQ'
           else 'US'
         end,
         v_currency)
      returning id into v_company_id;

      insert into public.earnings_company_identifiers
        (company_id, identifier_type, identifier_value, valid_from)
      values (v_company_id, v_identifier_type, v_ticker, p_observed_on);
    else
      update public.earnings_companies
      set company_name = v_name,
          ticker = v_ticker,
          is_active = true,
          updated_at = now()
      where id = v_company_id;
    end if;

    insert into pg_temp.incoming_earnings_universe (company_id, rank)
    values (v_company_id, v_rank);

    insert into public.earnings_universe_snapshots
      (index_id, observed_on, company_id, rank, market_cap,
       market_cap_currency, source, source_reference)
    values
      (p_index_id, p_observed_on, v_company_id, v_rank, v_market_cap,
       v_currency, p_source, p_source_reference)
    on conflict (index_id, observed_on, company_id) do update set
      rank = excluded.rank,
      market_cap = excluded.market_cap,
      market_cap_currency = excluded.market_cap_currency,
      source = excluded.source,
      source_reference = excluded.source_reference;
  end loop;

  -- Same-day replacement can retract a membership created by an earlier run.
  -- Delete that zero-length candidate instead of producing effective_to before
  -- effective_from; older memberships remain as a closed historical interval.
  delete from public.earnings_index_memberships m
  where m.index_id = p_index_id
    and m.effective_from = p_observed_on
    and m.effective_to is null
    and not exists (
      select 1 from pg_temp.incoming_earnings_universe i where i.company_id = m.company_id
    );
  get diagnostics v_removed_same_day = row_count;

  update public.earnings_index_memberships m
  set effective_to = p_observed_on - 1,
      updated_at = now()
  where m.index_id = p_index_id
    and m.effective_from < p_observed_on
    and m.effective_to is null
    and not exists (
      select 1 from pg_temp.incoming_earnings_universe i where i.company_id = m.company_id
    );
  get diagnostics v_removed = row_count;
  v_removed := v_removed + v_removed_same_day;

  insert into public.earnings_index_memberships
    (index_id, company_id, effective_from, source, source_reference)
  select p_index_id, i.company_id, p_observed_on, p_source, p_source_reference
  from pg_temp.incoming_earnings_universe i
  where not exists (
    select 1 from public.earnings_index_memberships m
    where m.index_id = p_index_id and m.company_id = i.company_id and m.effective_to is null
  );
  get diagnostics v_added = row_count;

  select count(*) into v_snapshot_count
  from public.earnings_universe_snapshots
  where index_id = p_index_id and observed_on = p_observed_on;

  return jsonb_build_object(
    'index_id', p_index_id,
    'observed_on', p_observed_on,
    'snapshot_count', v_snapshot_count,
    'added', v_added,
    'removed', v_removed
  );
end;
$$;

revoke all on function public.sync_earnings_market_cap_universe(text, date, jsonb, text, text)
  from public, anon, authenticated;
grant execute on function public.sync_earnings_market_cap_universe(text, date, jsonb, text, text)
  to service_role;

-- Edge Functions must accept both legacy JWT service keys and newer secret-key
-- formats. Authorization is therefore capability-based instead of comparing
-- two key strings that can legitimately differ during key rotation.
create or replace function public.authorize_earnings_ingestion()
returns boolean
language sql
stable
security invoker
set search_path = public
as $$ select true $$;

revoke all on function public.authorize_earnings_ingestion() from public, anon, authenticated;
grant execute on function public.authorize_earnings_ingestion() to service_role;
