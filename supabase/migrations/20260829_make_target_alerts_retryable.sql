-- Target alerts are a durable delivery queue. A transient Kakao failure must not
-- discard an already detected threshold crossing.
alter table public.alert_events
  drop constraint if exists alert_events_status_check;

alter table public.alert_events
  add constraint alert_events_status_check
  check (status in ('pending', 'sent', 'failed', 'skipped'));

alter table public.alert_events
  add column if not exists attempt_count integer not null default 0,
  add column if not exists last_attempt_at timestamptz,
  add column if not exists sent_at timestamptz;

create index if not exists alert_events_retry_idx
  on public.alert_events (status, created_at)
  where status in ('pending', 'failed');
