begin;
set local statement_timeout = '10s';
CREATE OR REPLACE FUNCTION public.save_historical_indicator_manual_pivot(p_case_code text, p_index_code text, p_series_code text, p_source_date date, p_pivot_date date, p_pivot_value double precision, p_relationship text, p_reason text, p_comment text, p_key_reference text, p_is_deleted boolean, p_user_id uuid)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  automatic_snapshot jsonb;
  prior record;
  next_keys jsonb;
  origin_index text;
begin
  if auth.role() is distinct from 'service_role' then raise exception 'Service role required'; end if;
  if not exists (select 1 from public.user_accounts where user_id=p_user_id and is_admin=true) then
    raise exception 'Administrator required';
  end if;
  if p_case_code is null or p_index_code not in ('SP500','NASDAQ_COMPOSITE','KOSPI')
    or p_series_code is null or p_source_date is null then
    raise exception 'Pivot identity required';
  end if;
  if not exists (select 1 from public.historical_cases where case_code=p_case_code) then
    raise exception 'Historical case does not exist';
  end if;

  -- Preserve the existing serialized transfer and deletion protection.
  lock table public.historical_indicator_pivots in share row exclusive mode;
  lock table public.historical_indicator_manual_pivots in share row exclusive mode;
  select * into prior from public.historical_indicator_manual_pivots
    where case_code=p_case_code and series_code=p_series_code and source_date=p_source_date;
  origin_index=coalesce(prior.index_code,p_index_code);
  select coalesce(jsonb_agg(to_jsonb(a) order by a.index_code,a.pivot_order),'[]'::jsonb)
    into automatic_snapshot
    from public.historical_indicator_pivots a
    where a.case_code=p_case_code and a.series_code=p_series_code
      and a.pivot_date=p_source_date;

  if p_is_deleted then
    if (prior.source_date is null or prior.is_deleted)
      and automatic_snapshot='[]'::jsonb then
      raise exception 'Pivot to delete does not exist';
    end if;
    insert into public.historical_indicator_manual_pivots
      (case_code,index_code,series_code,source_date,is_deleted,key_references,
       source_automatic_pivots,created_by,updated_by)
    values (p_case_code,origin_index,p_series_code,p_source_date,true,'{}'::jsonb,
      automatic_snapshot,p_user_id,p_user_id)
    on conflict (case_code,index_code,series_code,source_date) do update set
      is_deleted=true,key_references='{}'::jsonb,
      source_automatic_pivots=case when historical_indicator_manual_pivots.source_automatic_pivots='[]'::jsonb
        then excluded.source_automatic_pivots else historical_indicator_manual_pivots.source_automatic_pivots end,
      updated_by=excluded.updated_by,updated_at=now();
    delete from public.historical_indicator_pivots a
      where a.case_code=p_case_code and a.series_code=p_series_code
        and a.pivot_date in (p_source_date,prior.pivot_date);
    return;
  end if;

  if p_pivot_date is null or p_pivot_value is null
    or p_pivot_value::text in ('NaN','Infinity','-Infinity')
    or (p_relationship is not null and p_relationship not in ('positive','inverse','unclear'))
    or (p_key_reference is not null and p_key_reference not in ('START','PEAK','TROUGH','AUTO')) then
    raise exception 'Invalid pivot input';
  end if;
  if exists (
    select 1 from public.historical_indicator_manual_pivots m
    where m.case_code=p_case_code and m.series_code=p_series_code
      and m.source_date<>p_source_date
      and (m.source_date=p_pivot_date or m.pivot_date=p_pivot_date)
  ) then raise exception 'Manual pivot date already exists'; end if;

  next_keys=coalesce(prior.key_references,'{}'::jsonb)-p_index_code;
  if p_key_reference in ('START','PEAK','TROUGH') then
    if exists (
      select 1 from public.historical_indicator_manual_pivots m
      where m.case_code=p_case_code and m.series_code=p_series_code
        and m.source_date<>p_source_date and not m.is_deleted
        and m.key_references->>p_index_code=p_key_reference
    ) then raise exception 'Key pivot already exists for this indicator and reference'; end if;
    next_keys=next_keys||jsonb_build_object(p_index_code,p_key_reference);
  elsif p_key_reference is null then
    next_keys=next_keys||jsonb_build_object(p_index_code,false);
  end if;

  insert into public.historical_indicator_manual_pivots
    (case_code,index_code,series_code,source_date,pivot_date,pivot_value,
     relationship,reason,comment,key_references,is_deleted,source_automatic_pivots,created_by,updated_by)
  values (p_case_code,origin_index,p_series_code,p_source_date,p_pivot_date,p_pivot_value,
    p_relationship,nullif(trim(p_reason),''),nullif(trim(p_comment),''),next_keys,false,
    automatic_snapshot,p_user_id,p_user_id)
  on conflict (case_code,index_code,series_code,source_date) do update set
    pivot_date=excluded.pivot_date,pivot_value=excluded.pivot_value,
    relationship=excluded.relationship,reason=excluded.reason,comment=excluded.comment,
    key_references=excluded.key_references,is_deleted=false,
    source_automatic_pivots=case when historical_indicator_manual_pivots.source_automatic_pivots='[]'::jsonb
      then excluded.source_automatic_pivots else historical_indicator_manual_pivots.source_automatic_pivots end,
    updated_by=excluded.updated_by,updated_at=now();
  delete from public.historical_indicator_pivots a
    where a.case_code=p_case_code and a.series_code=p_series_code
      and a.pivot_date in (p_source_date,p_pivot_date);
end;
$function$;
commit;
