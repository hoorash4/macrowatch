create table if not exists public.historical_indicator_ai_scores (
  case_code text not null,
  index_code text not null,
  series_code text not null,
  overall_score numeric not null default 0,
  meaningful_reference_count integer not null default 0,
  max_reference_score numeric not null default 0,
  reference_coverage_count integer not null default 0,
  coverage_bonus numeric not null default 0,
  cycle_relationship text not null default 'unresolved',
  by_reference jsonb not null default '{}'::jsonb,
  results jsonb not null default '[]'::jsonb,
  near_miss_pivots jsonb not null default '[]'::jsonb,
  ai_pivots jsonb not null default '[]'::jsonb,
  ai_regimes jsonb not null default '[]'::jsonb,
  ai_anomalies jsonb not null default '[]'::jsonb,
  scoring_version text not null,
  source_analyzed_at timestamptz not null,
  scored_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (case_code, index_code, series_code)
);

alter table public.historical_indicator_ai_scores enable row level security;
grant select on public.historical_indicator_ai_scores to anon, authenticated;

create policy "historical indicator ai scores are readable"
on public.historical_indicator_ai_scores
for select
to anon, authenticated
using (true);

create index if not exists historical_indicator_ai_scores_case_idx
on public.historical_indicator_ai_scores (case_code, index_code, overall_score desc);
