alter table earnings_v2.delisting_events
  add column if not exists effective_on date;

alter table earnings_v2.delisting_events
  drop constraint if exists delisting_events_event_type_check;

alter table earnings_v2.delisting_events
  add constraint delisting_events_event_type_check
  check (event_type in ('decision', 'final', 'absorbed_merger'));

create index if not exists earnings_v2_delisting_events_effective_date
  on earnings_v2.delisting_events (
    corp_code, (coalesce(effective_on, received_on))
  );

create or replace function public.earnings_v2_upsert_delisting_events(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.delisting_events as target (
    receipt_no, corp_code, received_on, report_name, event_type, effective_on
  )
  select receipt_no, corp_code, received_on, report_name, event_type, effective_on
  from jsonb_to_recordset(coalesce(p_rows, '[]'::jsonb)) as incoming(
    receipt_no text,
    corp_code text,
    received_on date,
    report_name text,
    event_type text,
    effective_on date
  )
  on conflict (receipt_no) do update set
    corp_code = excluded.corp_code,
    received_on = excluded.received_on,
    report_name = excluded.report_name,
    event_type = excluded.event_type,
    effective_on = excluded.effective_on,
    updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

drop function if exists public.earnings_v2_get_delisting_events(text[], date, date);

create function public.earnings_v2_get_delisting_events(
  p_corp_codes text[], p_start date, p_end date
)
returns table (
  receipt_no text,
  corp_code text,
  received_on date,
  report_name text,
  event_type text,
  effective_on date
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select event.receipt_no, event.corp_code, event.received_on,
         event.report_name, event.event_type, event.effective_on
  from earnings_v2.delisting_events event
  where event.corp_code = any(p_corp_codes)
    and coalesce(event.effective_on, event.received_on) between p_start and p_end
  order by coalesce(event.effective_on, event.received_on), event.receipt_no;
$$;

revoke all on function public.earnings_v2_upsert_delisting_events(jsonb)
  from public, anon, authenticated;
revoke all on function public.earnings_v2_get_delisting_events(text[], date, date)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_upsert_delisting_events(jsonb)
  to service_role;
grant execute on function public.earnings_v2_get_delisting_events(text[], date, date)
  to service_role;

comment on table earnings_v2.delisting_events is
  'OpenDART/KRX 상장폐지 공시와 구조화 회사합병 결정에서 확인한 피흡수합병 소멸 이벤트.';
