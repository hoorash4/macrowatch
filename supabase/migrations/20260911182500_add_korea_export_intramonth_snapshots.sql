create table if not exists public.korea_export_intramonth_snapshots (
  reference_month date not null,
  stage text not null check (stage in ('d10', 'd20', 'month_end')),
  period_end date not null,
  cumulative_export_musd numeric not null,
  cumulative_workdays numeric(6, 2) not null check (cumulative_workdays > 0),
  published_on date,
  source_url text not null,
  collected_at timestamptz not null default now(),
  primary key (reference_month, stage)
);

create index if not exists korea_export_intramonth_snapshots_period_end_idx
  on public.korea_export_intramonth_snapshots (period_end desc);

alter table public.korea_export_intramonth_snapshots enable row level security;

comment on table public.korea_export_intramonth_snapshots is
  'Official KCS cumulative 1-10, 1-20 and month-end export snapshots used to derive workday-adjusted independent intra-month export segments.';
