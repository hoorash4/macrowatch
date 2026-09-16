create table if not exists public.historical_indicator_ai_analysis (
  case_code text not null,
  index_code text not null,
  series_code text not null,
  display_start date not null,
  display_end date not null,
  cycle_start date not null,
  cycle_peak date,
  cycle_trough date,
  model text not null,
  prompt_version text not null,
  schema_version text not null,
  regimes jsonb not null default '[]'::jsonb,
  pivots jsonb not null default '[]'::jsonb,
  anomalies jsonb not null default '[]'::jsonb,
  source_point_count integer not null default 0,
  chart_sha256 text,
  analyzed_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (case_code, index_code, series_code),
  constraint historical_indicator_ai_analysis_case_fk foreign key (case_code) references public.historical_cases(case_code) on delete cascade
);

alter table public.historical_indicator_ai_analysis enable row level security;

create index if not exists historical_indicator_ai_analysis_series_idx
  on public.historical_indicator_ai_analysis(series_code, analyzed_at desc);
create index if not exists historical_indicator_ai_analysis_case_idx
  on public.historical_indicator_ai_analysis(case_code, index_code);
