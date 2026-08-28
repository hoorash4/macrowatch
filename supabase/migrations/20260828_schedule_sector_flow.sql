-- Keep sector ETF collection close to its Edge Function and database instead
-- of depending solely on GitHub's best-effort scheduled-event delivery.
create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net with schema extensions;

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

-- GitHub Actions remains an idempotent fallback. Each Supabase slot also has
-- a 15-minute retry; the dispatcher skips the retry after a completed refresh.
select cron.schedule(
  'macrowatch-sector-flow-open',
  '10,25 0 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{}'::jsonb
  );$$
);

select cron.schedule(
  'macrowatch-sector-flow-midday',
  '30,45 3 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{}'::jsonb
  );$$
);

select cron.schedule(
  'macrowatch-sector-flow-close',
  '40,55 6 * * 1-5',
  $$select net.http_post(
    url := 'https://xhghpywvthjuvespzdul.supabase.co/functions/v1/sector-flow-scheduler',
    headers := '{"Content-Type":"application/json"}'::jsonb,
    body := '{}'::jsonb
  );$$
);
