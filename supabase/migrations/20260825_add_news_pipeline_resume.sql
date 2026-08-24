alter table public.news_pipeline_runs
  add column if not exists next_offset integer not null default 0 check (next_offset >= 0);

alter table public.news_pipeline_runs
  alter column completed_at drop not null;
