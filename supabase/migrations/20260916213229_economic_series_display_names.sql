create table if not exists public.economic_chart_series_metadata (
  series_code text primary key,
  display_name text not null,
  updated_at timestamptz not null default now()
);

insert into public.economic_chart_series_metadata (series_code, display_name, updated_at)
values
  ('US_CORE_CPI', '미국 Core CPI', now()),
  ('US_CORE_PPI', '미국 Core PPI', now())
on conflict (series_code) do update
set display_name = excluded.display_name,
    updated_at = excluded.updated_at;

create or replace view public.economic_chart_series_coverage as
select p.series_code,
       min(p.observation_date) as first_date,
       max(p.observation_date) as last_date,
       count(*) as point_count,
       m.display_name
from public.economic_chart_series_points p
left join public.economic_chart_series_metadata m using (series_code)
group by p.series_code, m.display_name;

alter table public.economic_chart_series_metadata enable row level security;
drop policy if exists "economic chart series metadata is readable" on public.economic_chart_series_metadata;
create policy "economic chart series metadata is readable"
on public.economic_chart_series_metadata
for select
to anon, authenticated
using (true);
