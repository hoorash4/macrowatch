-- A policy decision is not a daily observation.  Preserve the genuinely daily
-- Treasury series, then retire the mixed daily policy-rate table.
create table if not exists public.us_treasury_10y_daily (
  observed_on date primary key,
  treasury_10y_pct numeric(8, 4) not null,
  source text not null default 'FRED:DGS10',
  updated_at timestamptz not null default now()
);

alter table public.us_treasury_10y_daily enable row level security;
drop policy if exists "Authenticated users can read U.S. Treasury 10Y daily" on public.us_treasury_10y_daily;
create policy "Authenticated users can read U.S. Treasury 10Y daily"
  on public.us_treasury_10y_daily for select to authenticated using (true);
grant select on table public.us_treasury_10y_daily to authenticated;
grant select, insert, update, delete on table public.us_treasury_10y_daily to service_role;

insert into public.us_treasury_10y_daily (observed_on, treasury_10y_pct, source, updated_at)
select observed_on, treasury_10y_pct, 'FRED:DGS10', updated_at
from public.us_policy_rate_daily
where treasury_10y_pct is not null
on conflict (observed_on) do update
set treasury_10y_pct = excluded.treasury_10y_pct,
    source = excluded.source,
    updated_at = excluded.updated_at;

do $$
begin
  if exists (select 1 from public.us_policy_rate_daily where treasury_10y_pct is not null)
     and not exists (select 1 from public.us_treasury_10y_daily) then
    raise exception 'Treasury migration did not preserve daily observations';
  end if;
end $$;

drop table public.us_policy_rate_daily;
