-- Administrator decisions are independent of replaceable automatic RDP results.
begin;
create table if not exists public.historical_indicator_manual_pivots (
  case_code text not null,
  index_code text not null check (index_code in ('SP500', 'NASDAQ_COMPOSITE', 'KOSPI')),
  series_code text not null,
  source_date date not null,
  pivot_date date,
  pivot_value double precision check (pivot_value::text not in ('NaN','Infinity','-Infinity')),
  relationship text check (relationship in ('positive', 'inverse')),
  reason text,
  comment text,
  key_reference text check (key_reference in ('START', 'PEAK', 'TROUGH')),
  is_deleted boolean not null default false,
  source_automatic_pivots jsonb not null default '[]'::jsonb
    check (jsonb_typeof(source_automatic_pivots) = 'array'),
  created_by uuid,
  created_at timestamptz not null default now(),
  updated_by uuid,
  updated_at timestamptz not null default now(),
  primary key (case_code, index_code, series_code, source_date),
  constraint historical_manual_pivot_payload check (
    is_deleted or (pivot_date is not null and pivot_value is not null
      and relationship is not null and nullif(trim(reason), '') is not null)
  )
);

create unique index if not exists historical_manual_pivot_date_unique
  on public.historical_indicator_manual_pivots(case_code,index_code,series_code,pivot_date)
  where pivot_date is not null;
create unique index if not exists historical_manual_pivot_key_unique
  on public.historical_indicator_manual_pivots(case_code,index_code,series_code,key_reference)
  where not is_deleted and key_reference is not null;

alter table public.historical_indicator_manual_pivots enable row level security;
grant select on public.historical_indicator_manual_pivots to authenticated;
drop policy if exists "Authenticated users read historical manual pivots" on public.historical_indicator_manual_pivots;
create policy "Authenticated users read historical manual pivots"
  on public.historical_indicator_manual_pivots for select to authenticated using (true);

create or replace function public.protect_historical_indicator_manual_pivots()
returns trigger language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' and auth.role() = 'service_role'
    and current_setting('macrowatch.explicit_manual_pivot_delete', true) = 'true' then
    return old;
  end if;
  raise exception 'Manual pivot records must not be physically deleted';
end;
$$;
drop trigger if exists protect_historical_manual_pivot_delete on public.historical_indicator_manual_pivots;
create trigger protect_historical_manual_pivot_delete
  before delete on public.historical_indicator_manual_pivots
  for each row execute function public.protect_historical_indicator_manual_pivots();
drop trigger if exists protect_historical_manual_pivot_truncate on public.historical_indicator_manual_pivots;
create trigger protect_historical_manual_pivot_truncate
  before truncate on public.historical_indicator_manual_pivots
  for each statement execute function public.protect_historical_indicator_manual_pivots();

create or replace function public.skip_automatic_pivot_at_manual_date()
returns trigger language plpgsql set search_path = '' as $$
begin
  if exists (
    select 1 from public.historical_indicator_manual_pivots m
    where m.case_code=new.case_code and m.index_code=new.index_code
      and m.series_code=new.series_code
      and (m.source_date=new.pivot_date or m.pivot_date=new.pivot_date)
  ) then return null; end if;
  return new;
end;
$$;
drop trigger if exists skip_automatic_pivot_at_manual_date on public.historical_indicator_pivots;
create trigger skip_automatic_pivot_at_manual_date
  before insert on public.historical_indicator_pivots
  for each row execute function public.skip_automatic_pivot_at_manual_date();

create or replace function public.save_historical_indicator_manual_pivot(
  p_case_code text, p_index_code text, p_series_code text, p_source_date date,
  p_pivot_date date, p_pivot_value double precision, p_relationship text,
  p_reason text, p_comment text, p_key_reference text, p_is_deleted boolean,
  p_user_id uuid
) returns void language plpgsql security definer set search_path = '' as $$
declare
  automatic_snapshot jsonb;
begin
  if auth.role() is distinct from 'service_role' then raise exception 'Service role required'; end if;
  if not exists (select 1 from public.user_accounts where user_id=p_user_id and is_admin=true) then
    raise exception 'Administrator required';
  end if;
  if p_case_code is null or p_index_code is null or p_series_code is null or p_source_date is null then
    raise exception 'Pivot identity required';
  end if;
  if not exists (select 1 from public.historical_cases where case_code=p_case_code) then
    raise exception 'Historical case does not exist';
  end if;
  -- Serialize transfer with the automatic full-replacement transaction.
  lock table public.historical_indicator_pivots in share row exclusive mode;
  select coalesce(jsonb_agg(to_jsonb(a) order by a.pivot_order),'[]'::jsonb)
  into automatic_snapshot
  from public.historical_indicator_pivots a
  where a.case_code=p_case_code and a.index_code=p_index_code and a.series_code=p_series_code
    and a.pivot_date=p_source_date;
  if p_is_deleted then
    if not exists (
      select 1 from public.historical_indicator_manual_pivots
      where case_code=p_case_code and index_code=p_index_code and series_code=p_series_code
        and source_date=p_source_date and not is_deleted
    ) and not exists (
      select 1 from public.historical_indicator_pivots
      where case_code=p_case_code and index_code=p_index_code and series_code=p_series_code
        and pivot_date=p_source_date
    ) then raise exception 'Pivot to delete does not exist'; end if;
    insert into public.historical_indicator_manual_pivots
      (case_code,index_code,series_code,source_date,is_deleted,source_automatic_pivots,created_by,updated_by)
    values (p_case_code,p_index_code,p_series_code,p_source_date,true,automatic_snapshot,p_user_id,p_user_id)
    on conflict (case_code,index_code,series_code,source_date) do update set
      is_deleted=true,
      source_automatic_pivots=case when historical_indicator_manual_pivots.source_automatic_pivots='[]'::jsonb
        then excluded.source_automatic_pivots else historical_indicator_manual_pivots.source_automatic_pivots end,
      updated_by=excluded.updated_by,updated_at=now();
    delete from public.historical_indicator_pivots a
    where a.case_code=p_case_code and a.index_code=p_index_code and a.series_code=p_series_code
      and (a.pivot_date=p_source_date or a.pivot_date=(
        select m.pivot_date from public.historical_indicator_manual_pivots m
        where m.case_code=p_case_code and m.index_code=p_index_code
          and m.series_code=p_series_code and m.source_date=p_source_date));
    return;
  end if;
  if p_pivot_date is null or p_pivot_value is null
    or p_pivot_value::text in ('NaN','Infinity','-Infinity')
    or p_relationship is null or p_relationship not in ('positive','inverse')
    or nullif(trim(p_reason),'') is null
    or (p_key_reference is not null and p_key_reference not in ('START','PEAK','TROUGH')) then
    raise exception 'Invalid pivot input';
  end if;
  if exists (
    select 1 from public.historical_indicator_manual_pivots
    where case_code=p_case_code and index_code=p_index_code and series_code=p_series_code
      and source_date<>p_source_date
      and (source_date=p_pivot_date or pivot_date=p_pivot_date)
  ) then raise exception 'Manual pivot date already exists'; end if;
  if p_key_reference is not null and exists (
    select 1 from public.historical_indicator_manual_pivots
    where case_code=p_case_code and index_code=p_index_code and series_code=p_series_code
      and key_reference=p_key_reference and source_date<>p_source_date and not is_deleted
  ) then raise exception 'Key pivot already exists for this indicator and reference'; end if;
  insert into public.historical_indicator_manual_pivots
    (case_code,index_code,series_code,source_date,pivot_date,pivot_value,relationship,reason,comment,key_reference,is_deleted,source_automatic_pivots,created_by,updated_by)
  values (p_case_code,p_index_code,p_series_code,p_source_date,p_pivot_date,p_pivot_value,
    p_relationship,trim(p_reason),nullif(trim(p_comment),''),p_key_reference,false,automatic_snapshot,p_user_id,p_user_id)
  on conflict (case_code,index_code,series_code,source_date) do update set
    pivot_date=excluded.pivot_date,pivot_value=excluded.pivot_value,relationship=excluded.relationship,
    reason=excluded.reason,comment=excluded.comment,key_reference=excluded.key_reference,
    is_deleted=false,
    source_automatic_pivots=case when historical_indicator_manual_pivots.source_automatic_pivots='[]'::jsonb
      then excluded.source_automatic_pivots else historical_indicator_manual_pivots.source_automatic_pivots end,
    updated_by=excluded.updated_by,updated_at=now();
  delete from public.historical_indicator_pivots
  where case_code=p_case_code and index_code=p_index_code and series_code=p_series_code
    and pivot_date in (p_source_date,p_pivot_date);
end;
$$;
revoke all on function public.save_historical_indicator_manual_pivot(text,text,text,date,date,double precision,text,text,text,text,boolean,uuid) from public,anon,authenticated;
grant execute on function public.save_historical_indicator_manual_pivot(text,text,text,date,date,double precision,text,text,text,text,boolean,uuid) to service_role;

create or replace function public.delete_historical_case_with_manual_choice(
  p_case_code text, p_delete_manual_pivots boolean, p_user_id uuid
) returns boolean language plpgsql security definer set search_path = '' as $$
declare
  deleted_count integer;
begin
  if auth.role() is distinct from 'service_role' then raise exception 'Service role required'; end if;
  if not exists (select 1 from public.user_accounts where user_id=p_user_id and is_admin=true) then
    raise exception 'Administrator required';
  end if;
  if p_case_code is null or nullif(trim(p_case_code),'') is null then
    raise exception 'Case code required';
  end if;
  if not exists (select 1 from public.historical_cases where case_code=p_case_code) then
    return false;
  end if;
  if p_delete_manual_pivots then
    perform set_config('macrowatch.explicit_manual_pivot_delete','true',true);
    delete from public.historical_indicator_manual_pivots where case_code=p_case_code;
  end if;
  delete from public.historical_cases where case_code=p_case_code;
  get diagnostics deleted_count = row_count;
  return deleted_count > 0;
end;
$$;
revoke all on function public.delete_historical_case_with_manual_choice(text,boolean,uuid) from public,anon,authenticated;
grant execute on function public.delete_historical_case_with_manual_choice(text,boolean,uuid) to service_role;
commit;

