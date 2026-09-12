-- Sector-flow automatic execution has one scheduler: Supabase pg_cron.
-- Each stage has an explicit primary and +15 minute retry job. The stage is
-- carried in the request body, so changing a clock time can never change phase.

do $$
declare
  existing_job record;
begin
  for existing_job in
    select jobid from cron.job where jobname like 'macrowatch-sector-flow-%'
  loop
    perform cron.unschedule(existing_job.jobid);
  end loop;
end
$$;

select cron.schedule(
  'macrowatch-sector-flow-open-primary',
  '10 0 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{"stage":"open"}'::jsonb
  );$$
);
select cron.schedule(
  'macrowatch-sector-flow-open-retry',
  '25 0 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{"stage":"open"}'::jsonb
  );$$
);

select cron.schedule(
  'macrowatch-sector-flow-intraday-primary',
  '30 3 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{"stage":"intraday"}'::jsonb
  );$$
);
select cron.schedule(
  'macrowatch-sector-flow-intraday-retry',
  '45 3 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{"stage":"intraday"}'::jsonb
  );$$
);

select cron.schedule(
  'macrowatch-sector-flow-close-primary',
  '40 6 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{"stage":"close"}'::jsonb
  );$$
);
select cron.schedule(
  'macrowatch-sector-flow-close-retry',
  '55 6 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{"stage":"close"}'::jsonb
  );$$
);

create or replace function public.macrowatch_sector_flow_schedules()
returns table(jobname text, schedule text, active boolean, latest_success_at timestamptz)
language sql
security definer
set search_path = pg_catalog, public, cron
as $$
  select
    j.jobname::text,
    j.schedule::text,
    j.active,
    case
      when j.jobname like 'macrowatch-sector-flow-open-%' then (
        select max(r.calculated_at) from public.market_sector_weekly_rankings r where r.price_stage = 'open'
      )
      when j.jobname like 'macrowatch-sector-flow-intraday-%' then (
        select max(r.calculated_at) from public.market_sector_weekly_rankings r where r.price_stage = 'intraday'
      )
      when j.jobname like 'macrowatch-sector-flow-close-%' then (
        select max(r.calculated_at) from public.market_sector_weekly_rankings r where r.price_stage = 'close'
      )
      else null
    end as latest_success_at
  from cron.job as j
  where j.jobname like 'macrowatch-sector-flow-%'
  order by j.jobname;
$$;

revoke all on function public.macrowatch_sector_flow_schedules() from public, anon, authenticated;
grant execute on function public.macrowatch_sector_flow_schedules() to service_role;

create or replace function public.macrowatch_set_sector_flow_schedule(
  p_stage text,
  p_primary_schedule text,
  p_retry_schedule text
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, cron
as $$
declare
  primary_id bigint;
  retry_id bigint;
begin
  if p_stage not in ('open', 'intraday', 'close') then
    raise exception 'invalid sector-flow stage';
  end if;
  if p_primary_schedule !~ '^([0-5]?[0-9]) ([0-9]|1[0-9]|2[0-3]) \* \* 1-5$'
     or p_retry_schedule !~ '^([0-5]?[0-9]) ([0-9]|1[0-9]|2[0-3]) \* \* 1-5$' then
    raise exception 'invalid sector-flow schedule';
  end if;

  select jobid into primary_id from cron.job
  where jobname = 'macrowatch-sector-flow-' || p_stage || '-primary';
  select jobid into retry_id from cron.job
  where jobname = 'macrowatch-sector-flow-' || p_stage || '-retry';
  if primary_id is null or retry_id is null then
    raise exception 'sector-flow cron job is missing for stage %', p_stage;
  end if;

  perform cron.alter_job(primary_id, schedule := p_primary_schedule);
  perform cron.alter_job(retry_id, schedule := p_retry_schedule);
end;
$$;

revoke all on function public.macrowatch_set_sector_flow_schedule(text, text, text) from public, anon, authenticated;
grant execute on function public.macrowatch_set_sector_flow_schedule(text, text, text) to service_role;

create or replace function public.macrowatch_set_sector_flow_enabled(
  p_stage text,
  p_enabled boolean
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, cron
as $$
declare
  primary_id bigint;
  retry_id bigint;
begin
  if p_stage not in ('open', 'intraday', 'close') then
    raise exception 'invalid sector-flow stage';
  end if;
  select jobid into primary_id from cron.job
  where jobname = 'macrowatch-sector-flow-' || p_stage || '-primary';
  select jobid into retry_id from cron.job
  where jobname = 'macrowatch-sector-flow-' || p_stage || '-retry';
  if primary_id is null or retry_id is null then
    raise exception 'sector-flow cron job is missing for stage %', p_stage;
  end if;
  perform cron.alter_job(primary_id, active := p_enabled);
  perform cron.alter_job(retry_id, active := p_enabled);
end;
$$;

revoke all on function public.macrowatch_set_sector_flow_enabled(text, boolean) from public, anon, authenticated;
grant execute on function public.macrowatch_set_sector_flow_enabled(text, boolean) to service_role;
