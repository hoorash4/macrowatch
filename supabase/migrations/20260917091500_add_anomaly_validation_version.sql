alter table public.historical_indicator_ai_analysis
  add column if not exists anomaly_validation_version text null;

comment on column public.historical_indicator_ai_analysis.anomaly_validation_version
  is 'Anomaly validation method/version. NULL means legacy analysis without enforced event/web validation.';
