-- Reusable external observations and internally calculated chart series have
-- separate write boundaries.  The read view preserves one frontend contract.

create table if not exists public.economic_chart_derived_points (
  series_code text not null,
  observation_date date not null,
  value numeric not null,
  frequency text not null check (frequency in ('D','W','M','Q','E','T')),
  source text not null check (
    source like 'DERIVED:%' or source like 'RESAMPLED:%' or source like 'INTERNAL:%'
  ),
  calculated_at timestamptz not null default now(),
  primary key (series_code, observation_date)
);

create index if not exists economic_chart_derived_points_date_idx
  on public.economic_chart_derived_points (series_code, observation_date desc);

alter table public.economic_chart_derived_points enable row level security;
drop policy if exists "Authenticated users can read derived chart points"
  on public.economic_chart_derived_points;
create policy "Authenticated users can read derived chart points"
  on public.economic_chart_derived_points for select to authenticated using (true);

-- A nowcast vintage has both a target month and an observation date, so it
-- cannot be represented faithfully by the one-date canonical series shape.
create table if not exists public.inflation_nowcast_vintages (
  kind text not null check (kind in ('headline','core')),
  target_month date not null,
  observed_on date not null,
  cpi_yoy_pct numeric not null,
  pce_yoy_pct numeric not null,
  source text not null default 'CLEVELAND_FED:inflation_nowcasting',
  collected_at timestamptz not null default now(),
  primary key (kind, target_month, observed_on)
);
alter table public.inflation_nowcast_vintages enable row level security;
drop policy if exists "Authenticated users can read inflation nowcast sources"
  on public.inflation_nowcast_vintages;
create policy "Authenticated users can read inflation nowcast sources"
  on public.inflation_nowcast_vintages for select to authenticated using (true);

insert into public.economic_chart_derived_points
  (series_code, observation_date, value, frequency, source, calculated_at)
select series_code, observation_date, value, frequency,
       case when source like 'DERIVED:%' or source like 'RESAMPLED:%' or source like 'INTERNAL:%'
            then source
            when series_code in ('SP500_MONTH_END','SP500_WEEKLY_CLOSE','KOSPI_MONTH_END',
                                 'KOSPI_WEEKLY_CLOSE','HY_OAS_WEEKLY','EEM_WEEKLY_CLOSE')
            then 'RESAMPLED:' || source
            else 'DERIVED:' || source end,
       collected_at
from public.economic_chart_points
where series_code in (
  'US10Y2Y', 'KR10Y3Y', 'US_POLICY_RATE_MID', 'KR_EXPORT_DAILY_AVG',
  'SP500_MONTH_END', 'SP500_WEEKLY_CLOSE', 'KOSPI_MONTH_END', 'KOSPI_WEEKLY_CLOSE',
  'HY_OAS_WEEKLY', 'EEM_WEEKLY_CLOSE', 'EM_HY_OAS_4W', 'EM_TAIL_RISK_OAS_4W',
  'VXEEM_4W', 'US_SHORT_FUNDING_SPREAD'
)
on conflict (series_code, observation_date) do update
set value = excluded.value, frequency = excluded.frequency,
    source = excluded.source, calculated_at = excluded.calculated_at;

delete from public.economic_chart_points
where series_code in (
  'US10Y2Y', 'KR10Y3Y', 'US_POLICY_RATE_MID', 'KR_EXPORT_DAILY_AVG',
  'SP500_MONTH_END', 'SP500_WEEKLY_CLOSE', 'KOSPI_MONTH_END', 'KOSPI_WEEKLY_CLOSE',
  'HY_OAS_WEEKLY', 'EEM_WEEKLY_CLOSE', 'EM_HY_OAS_4W', 'EM_TAIL_RISK_OAS_4W',
  'VXEEM_4W', 'US_SHORT_FUNDING_SPREAD'
);

alter table public.economic_chart_points
  drop constraint if exists economic_chart_points_external_source_only;
alter table public.economic_chart_points
  add constraint economic_chart_points_external_source_only check (
    source not like 'DERIVED:%' and source not like 'RESAMPLED:%' and source not like 'INTERNAL:%'
  );

-- Synthetic weekly Friday rows left by the retired cache migration are not
-- source observations.  Daily source rows remain untouched.
delete from public.economic_chart_points
where series_code in ('US10Y', 'KR10Y') and frequency = 'W';

delete from public.economic_chart_points
where series_code in ('EM_HY_OAS', 'EM_TAIL_RISK_OAS', 'VXEEM') and frequency = 'W';

delete from public.economic_chart_points
where series_code in ('KR_BBB_YIELD','KR_AA_YIELD','KR_CP91','KR_CD91','KR_KORIBOR3M')
  and frequency = 'M';

create or replace function public.enforce_economic_series_storage_class()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_table_name = 'economic_chart_points' and exists (
    select 1 from public.economic_chart_derived_points d where d.series_code = new.series_code
  ) then
    raise exception 'Series % is registered as derived', new.series_code;
  end if;
  if tg_table_name = 'economic_chart_derived_points' and exists (
    select 1 from public.economic_chart_points s where s.series_code = new.series_code
  ) then
    raise exception 'Series % is registered as source', new.series_code;
  end if;
  return new;
end;
$$;

drop trigger if exists economic_chart_points_storage_class_guard on public.economic_chart_points;
create trigger economic_chart_points_storage_class_guard
before insert or update of series_code on public.economic_chart_points
for each row execute function public.enforce_economic_series_storage_class();

drop trigger if exists economic_chart_derived_points_storage_class_guard on public.economic_chart_derived_points;
create trigger economic_chart_derived_points_storage_class_guard
before insert or update of series_code on public.economic_chart_derived_points
for each row execute function public.enforce_economic_series_storage_class();

revoke all on function public.enforce_economic_series_storage_class() from public, anon, authenticated;

create or replace view public.economic_chart_series_points
with (security_invoker = true) as
select series_code, observation_date, value, frequency, source, collected_at
from public.economic_chart_points
union all
select series_code, observation_date, value, frequency, source,
       calculated_at as collected_at
from public.economic_chart_derived_points;

revoke all on table public.economic_chart_derived_points from anon;
grant select on table public.economic_chart_derived_points to authenticated;
grant all on table public.economic_chart_derived_points to service_role;
revoke all on table public.inflation_nowcast_vintages from anon;
grant select on table public.inflation_nowcast_vintages to authenticated;
grant all on table public.inflation_nowcast_vintages to service_role;
revoke all on table public.economic_chart_series_points from anon;
grant select on table public.economic_chart_series_points to authenticated;
grant select on table public.economic_chart_series_points to service_role;

comment on table public.economic_chart_points is
  'Canonical observations received from external source systems. Internally calculated series are excluded.';
comment on table public.economic_chart_derived_points is
  'Reusable chart series calculated only from stored canonical observations or other stored source records.';
comment on view public.economic_chart_series_points is
  'Read-only compatibility surface combining canonical source and reusable derived chart series.';
comment on table public.inflation_nowcast_vintages is
  'Published Cleveland Fed inflation nowcast vintages; the inflation composite reads this stored source without provider calls.';

drop function if exists public.store_liquidity_batch(text, jsonb, jsonb);

