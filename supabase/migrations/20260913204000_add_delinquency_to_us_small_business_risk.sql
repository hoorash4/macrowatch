alter table public.us_small_business_risk_monthly
  add column if not exists small_business_delinquency_pct numeric(8, 4),
  add column if not exists delinquency_source_month date;

alter table public.us_small_business_risk_monthly
  drop column if exists survey_risk_index,
  drop column if exists financing_risk_index,
  drop column if exists high_yield_oas_pct,
  drop column if exists includes_oas;

comment on table public.us_small_business_risk_monthly is
  'Monthly U.S. small-business risk index: NFIB borrowing difficulty 35%, Equifax small-business delinquency 35%, and NFIB sales expectations 30%.';

comment on column public.us_small_business_risk_monthly.small_business_delinquency_pct is
  'Stored US_SBDI_31_180 value used for this risk-index observation.';

comment on column public.us_small_business_risk_monthly.delinquency_source_month is
  'Observation month of the stored US_SBDI_31_180 value used in this row.';
