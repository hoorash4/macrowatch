alter table public.us_small_business_risk_monthly
  add column if not exists survey_risk_index numeric(8, 4);

update public.us_small_business_risk_monthly
set survey_risk_index = round((
  greatest(0::numeric, (borrowing_difficulty_pct - 2) / 13 * 100) * 0.60
  + greatest(0::numeric, (sales_expectation_net - 20) / -70 * 100) * 0.40
)::numeric, 2);

comment on column public.us_small_business_risk_monthly.survey_risk_index is
  'NFIB borrowing difficulty 60% and sales expectations 40% risk index; excludes OAS';
