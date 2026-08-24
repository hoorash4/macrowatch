create table if not exists public.news_pipeline_runs (
  run_date date primary key,
  completed_at timestamptz not null default now()
);

alter table public.news_pipeline_runs enable row level security;

comment on table public.news_pipeline_runs is 'Completed daily news pipeline runs; prevents fallback schedules from duplicating a completed day.';
