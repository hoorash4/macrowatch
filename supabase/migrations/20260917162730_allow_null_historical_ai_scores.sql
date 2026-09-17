alter table public.historical_indicator_ai_scores
  alter column overall_score drop not null,
  alter column max_reference_score drop not null;
