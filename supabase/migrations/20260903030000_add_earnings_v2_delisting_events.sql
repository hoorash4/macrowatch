-- Delisting decisions are durable collection events.  They are stored apart
-- from the frozen quarterly universe so a decision discovered before quarter
-- end can resolve that quarter when its earnings collection begins later.

create table if not exists earnings_v2.delisting_events (
  receipt_no text primary key,
  corp_code text not null,
  received_on date not null,
  report_name text not null,
  event_type text not null check (event_type in ('decision', 'final')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (receipt_no ~ '^[0-9]{14}$'),
  check (corp_code ~ '^[0-9]{8}$')
);

create index if not exists earnings_v2_delisting_events_company_date
  on earnings_v2.delisting_events(corp_code, received_on);

alter table earnings_v2.delisting_events enable row level security;
revoke all on table earnings_v2.delisting_events from public, anon, authenticated;
grant select, insert, update on table earnings_v2.delisting_events to service_role;

create or replace function public.earnings_v2_upsert_delisting_events(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.delisting_events as target (
    receipt_no, corp_code, received_on, report_name, event_type
  )
  select receipt_no, corp_code, received_on, report_name, event_type
  from jsonb_to_recordset(coalesce(p_rows, '[]'::jsonb)) as incoming(
    receipt_no text,
    corp_code text,
    received_on date,
    report_name text,
    event_type text
  )
  on conflict (receipt_no) do update set
    corp_code = excluded.corp_code,
    received_on = excluded.received_on,
    report_name = excluded.report_name,
    event_type = excluded.event_type,
    updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_get_delisting_events(
  p_corp_codes text[], p_start date, p_end date
)
returns table (
  receipt_no text,
  corp_code text,
  received_on date,
  report_name text,
  event_type text
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select event.receipt_no, event.corp_code, event.received_on,
         event.report_name, event.event_type
  from earnings_v2.delisting_events event
  where event.corp_code = any(p_corp_codes)
    and event.received_on between p_start and p_end
  order by event.received_on, event.receipt_no;
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
  'Exact OpenDART exchange disclosures named 상장폐지결정 or 상장폐지.';

