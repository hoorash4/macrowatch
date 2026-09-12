create table if not exists public.automatic_source_points (
  collector text not null,
  series_code text not null,
  period_date date not null,
  observed_on date not null,
  value numeric(20, 8) not null,
  updated_at timestamptz not null default now(),
  primary key (collector, series_code, period_date)
);

create index if not exists automatic_source_points_collector_date_idx
  on public.automatic_source_points (collector, period_date desc);

alter table public.automatic_source_points enable row level security;
revoke all on public.automatic_source_points from public, anon, authenticated;
grant select, insert, update, delete on public.automatic_source_points to service_role;

comment on table public.automatic_source_points is
  'Private historical source cache used by scheduled calculations so automatic runs fetch only recent observations. Historical initialization is explicit.';
