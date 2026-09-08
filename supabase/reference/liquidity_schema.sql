-- Applied with Supabase MCP migration: add_liquidity_indices_v1.
create table public.liquidity_observations (
  country text not null check (country in ('US', 'KR')),
  series text not null,
  observation_date date not null,
  value double precision not null check (value > '-Infinity'::float8 and value < 'Infinity'::float8),
  collected_at timestamptz not null default now(),
  primary key (country, series, observation_date)
);
create table public.liquidity_indices (
  country text not null check (country in ('US', 'KR')),
  metric text not null check (metric in ('pressure', 'capacity')),
  observation_date date not null,
  score double precision not null check (score between 0 and 100),
  components jsonb not null,
  component_scores jsonb not null,
  sample_count integer not null check (sample_count > 0),
  is_warmup boolean not null,
  frequency text not null check (frequency in ('D', 'W', 'M')),
  method_version text not null,
  calculated_at timestamptz not null default now(),
  primary key (country, metric, observation_date)
);
alter table public.liquidity_observations enable row level security;
alter table public.liquidity_indices enable row level security;
revoke all on public.liquidity_observations, public.liquidity_indices from anon, authenticated;
grant select on public.liquidity_indices to anon, authenticated;
create policy liquidity_public_read on public.liquidity_indices for select to anon, authenticated using (true);
grant select, insert on public.liquidity_observations, public.liquidity_indices to service_role;

create function public.store_liquidity_batch(p_country text, p_raw jsonb, p_results jsonb)
returns void language plpgsql security invoker set search_path = '' as $$
begin
  if p_country not in ('US', 'KR') or jsonb_array_length(p_results) = 0 then
    raise exception 'Invalid liquidity batch';
  end if;
  if exists (select 1 from jsonb_array_elements(p_raw || p_results) r where r->>'country' is distinct from p_country) then
    raise exception 'Country mismatch';
  end if;
  insert into public.liquidity_observations(country, series, observation_date, value)
  select country, series, observation_date, value
  from jsonb_to_recordset(p_raw) as r(country text, series text, observation_date date, value double precision)
  on conflict (country, series, observation_date) do nothing;
  insert into public.liquidity_indices(country, metric, observation_date, score, components,
    component_scores, sample_count, is_warmup, frequency, method_version)
  select country, metric, observation_date, score, components, component_scores,
    sample_count, is_warmup, frequency, method_version
  from jsonb_to_recordset(p_results) as r(country text, metric text, observation_date date,
    score double precision, components jsonb, component_scores jsonb, sample_count integer,
    is_warmup boolean, frequency text, method_version text)
  on conflict (country, metric, observation_date) do nothing;
end;
$$;
revoke all on function public.store_liquidity_batch(text,jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.store_liquidity_batch(text,jsonb,jsonb) to service_role;
