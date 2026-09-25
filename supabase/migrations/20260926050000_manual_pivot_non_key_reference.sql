begin;
set local statement_timeout = '10s';

create or replace function public.save_historical_indicator_manual_pivot(
  p_case_code text,
  p_index_code text,
  p_series_code text,
  p_source_date date,
  p_pivot_date date,
  p_pivot_value double precision,
  p_relationship text,
  p_reason text,
  p_comment text,
  p_key_reference text,
  p_is_deleted boolean,
  p_user_id uuid
) returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  automatic_snapshot jsonb;
  prior record;
  next_keys jsonb;
  origin_index text;
  target_date date;
  pivot_found boolean;
begin
  if auth.role() is distinct from 'service_role' then
    raise exception 'Service role required';
  end if;
  if not exists (select 1 from public.user_accounts where user_id = p_user_id and is_admin = true) then
    raise exception 'Administrator required';
  end if;
  if p_case_code is null or p_index_code not in ('SP500', 'NASDAQ_COMPOSITE', 'KOSPI')
    or p_series_code is null or p_source_date is null then
    raise exception 'Pivot identity required';
  end if;
  if not exists (select 1 from public.historical_cases where case_code = p_case_code) then
    raise exception 'Historical case does not exist';
  end if;

  lock table public.historical_indicator_pivots in share row exclusive mode;
  lock table public.historical_indicator_manual_pivots in share row exclusive mode;
  select * into prior from public.historical_indicator_manual_pivots
    where case_code = p_case_code and series_code = p_series_code and source_date = p_source_date;
  origin_index = coalesce(prior.index_code, p_index_code);

  if p_is_deleted then
    target_date = coalesce(prior.pivot_date, p_source_date);
    select exists (
      select 1 from public.historical_indicator_pivots a
        where a.case_code = p_case_code and a.series_code = p_series_code and a.pivot_date = target_date
      union all
      select 1 from public.historical_indicator_ai_scores s
        cross join lateral jsonb_array_elements(s.auto_pivots) pivot(value)
        where s.case_code = p_case_code and s.series_code = p_series_code
          and pivot.value->>'date' = target_date::text
      union all
      select 1 from public.historical_indicator_ai_analysis a
        cross join lateral jsonb_array_elements(a.pivots) pivot(value)
        where a.case_code = p_case_code and a.series_code = p_series_code
          and pivot.value->>'date' = target_date::text
    ) into pivot_found;
    if prior.source_date is null and not pivot_found then
      raise exception 'Pivot to delete does not exist';
    end if;
    if prior.source_date is not null then
      perform set_config('macrowatch.explicit_manual_pivot_delete', 'true', true);
      delete from public.historical_indicator_manual_pivots m
        where m.case_code = p_case_code and m.series_code = p_series_code
          and m.source_date = p_source_date;
      perform set_config('macrowatch.explicit_manual_pivot_delete', 'false', true);
    end if;
  else
    if p_pivot_date is null or p_pivot_value is null
      or p_pivot_value::text in ('NaN', 'Infinity', '-Infinity')
      or (p_relationship is not null and p_relationship not in ('positive', 'inverse', 'unclear'))
      or (p_key_reference is not null and p_key_reference not in ('START', 'PEAK', 'TROUGH', 'AUTO', 'START_REF', 'PEAK_REF', 'TROUGH_REF')) then
      raise exception 'Invalid pivot input';
    end if;
    if exists (
      select 1 from public.historical_indicator_manual_pivots m
      where m.case_code = p_case_code and m.series_code = p_series_code
        and m.source_date <> p_source_date
        and (m.source_date = p_pivot_date or m.pivot_date = p_pivot_date)
    ) then
      raise exception 'Manual pivot date already exists';
    end if;

    select coalesce(jsonb_agg(to_jsonb(a) order by a.index_code, a.pivot_order), '[]'::jsonb)
      into automatic_snapshot
      from public.historical_indicator_pivots a
      where a.case_code = p_case_code and a.series_code = p_series_code
        and a.pivot_date = p_source_date;
    if automatic_snapshot = '[]'::jsonb then
      select jsonb_build_array(pivot.value) into automatic_snapshot
      from public.historical_indicator_ai_scores s
      cross join lateral jsonb_array_elements(s.auto_pivots) pivot(value)
      where s.case_code = p_case_code and s.series_code = p_series_code
        and pivot.value->>'date' = p_source_date::text
      order by case when s.index_code = p_index_code then 0 else 1 end, s.index_code
      limit 1;
      automatic_snapshot = coalesce(automatic_snapshot, '[]'::jsonb);
    end if;
    if automatic_snapshot = '[]'::jsonb then
      select jsonb_build_array(pivot.value) into automatic_snapshot
      from public.historical_indicator_ai_analysis a
      cross join lateral jsonb_array_elements(a.pivots) pivot(value)
      where a.case_code = p_case_code and a.series_code = p_series_code
        and pivot.value->>'date' = p_source_date::text
      order by case when a.index_code = p_index_code then 0 else 1 end, a.index_code
      limit 1;
      automatic_snapshot = coalesce(automatic_snapshot, '[]'::jsonb);
    end if;

    next_keys = coalesce(prior.key_references, '{}'::jsonb) - p_index_code;
    if p_key_reference in ('START', 'PEAK', 'TROUGH') then
      if exists (
        select 1 from public.historical_indicator_manual_pivots m
        where m.case_code = p_case_code and m.series_code = p_series_code
          and m.source_date <> p_source_date
          and m.key_references->>p_index_code = p_key_reference
      ) then
        raise exception 'Key pivot already exists for this indicator and reference';
      end if;
      next_keys = next_keys || jsonb_build_object(p_index_code, p_key_reference);
    elsif p_key_reference in ('START_REF', 'PEAK_REF', 'TROUGH_REF') then
      next_keys = next_keys || jsonb_build_object(p_index_code, p_key_reference);
    elsif p_key_reference is null then
      next_keys = next_keys || jsonb_build_object(p_index_code, false);
    end if;

    if prior.source_date is not null and p_source_date <> p_pivot_date then
      update public.historical_indicator_manual_pivots m
        set source_date = p_pivot_date
        where m.case_code = p_case_code and m.series_code = p_series_code
          and m.source_date = p_source_date;
    end if;

    insert into public.historical_indicator_manual_pivots
      (case_code, index_code, series_code, source_date, pivot_date, pivot_value,
       relationship, reason, comment, key_references, source_automatic_pivots, created_by, updated_by)
    values (p_case_code, origin_index, p_series_code, p_pivot_date, p_pivot_date, p_pivot_value,
      p_relationship, nullif(trim(p_reason), ''), nullif(trim(p_comment), ''), next_keys,
      automatic_snapshot, p_user_id, p_user_id)
    on conflict (case_code, index_code, series_code, source_date) do update set
      pivot_date = excluded.pivot_date,
      pivot_value = excluded.pivot_value,
      relationship = excluded.relationship,
      reason = excluded.reason,
      comment = excluded.comment,
      key_references = excluded.key_references,
      source_automatic_pivots = case
        when historical_indicator_manual_pivots.source_automatic_pivots = '[]'::jsonb
          then excluded.source_automatic_pivots
        else historical_indicator_manual_pivots.source_automatic_pivots
      end,
      updated_by = excluded.updated_by,
      updated_at = now();
    target_date = p_source_date;
  end if;

  delete from public.historical_indicator_pivots a
    where a.case_code = p_case_code and a.series_code = p_series_code
      and a.pivot_date = target_date;
  update public.historical_indicator_ai_scores s
    set auto_pivots = coalesce((
      select jsonb_agg(pivot.value order by pivot.ordinality)
      from jsonb_array_elements(s.auto_pivots) with ordinality pivot(value, ordinality)
      where pivot.value->>'date' is distinct from target_date::text
    ), '[]'::jsonb)
    where s.case_code = p_case_code and s.series_code = p_series_code;
  update public.historical_indicator_ai_analysis a
    set pivots = coalesce((
      select jsonb_agg(pivot.value order by pivot.ordinality)
      from jsonb_array_elements(a.pivots) with ordinality pivot(value, ordinality)
      where pivot.value->>'date' is distinct from target_date::text
    ), '[]'::jsonb)
    where a.case_code = p_case_code and a.series_code = p_series_code;
end;
$$;

commit;
