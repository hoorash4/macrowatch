-- Preserve original results as a versioned audit; monthly v2 is served separately.
alter table public.liquidity_indices drop constraint liquidity_indices_pkey;
alter table public.liquidity_indices add primary key (country, metric, observation_date, method_version);
create or replace function public.store_liquidity_batch(p_country text, p_raw jsonb, p_results jsonb)
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
  on conflict (country, metric, observation_date, method_version) do nothing;
end;
$$;
revoke all on function public.store_liquidity_batch(text,jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.store_liquidity_batch(text,jsonb,jsonb) to service_role;

