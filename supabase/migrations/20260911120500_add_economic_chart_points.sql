create table if not exists public.economic_chart_points (
  series_code text not null,
  observation_date date not null,
  value numeric not null,
  frequency text not null check (frequency in ('D','W','T','M')),
  source text not null,
  collected_at timestamptz not null default now(),
  primary key (series_code, observation_date)
);

create index if not exists economic_chart_points_date_idx
  on public.economic_chart_points (series_code, observation_date desc);

alter table public.economic_chart_points enable row level security;

drop policy if exists "Authenticated users can read economic chart points" on public.economic_chart_points;
create policy "Authenticated users can read economic chart points"
  on public.economic_chart_points for select to authenticated using (true);

comment on table public.economic_chart_points is
  'Read-only chart store for raw/derived economic time series. Scheduled collectors append new observations only; historical bootstrap is an explicit manual operation.';
