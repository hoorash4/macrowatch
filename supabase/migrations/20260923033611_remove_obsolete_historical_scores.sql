update public.historical_indicator_ai_scores
set by_reference = '{}'::jsonb
where scoring_version <> 'historical-pivot-4-3-3-v2';

drop index if exists public.historical_indicator_ai_scores_case_idx;

alter table public.historical_indicator_ai_scores
  drop column if exists overall_score,
  drop column if exists meaningful_reference_count,
  drop column if exists max_reference_score,
  drop column if exists reference_coverage_count,
  drop column if exists coverage_bonus,
  drop column if exists cycle_relationship,
  drop column if exists results,
  drop column if exists near_miss_pivots,
  drop column if exists filtered_pivots;

create index if not exists historical_indicator_ai_scores_case_idx
  on public.historical_indicator_ai_scores (case_code, index_code, series_code);

