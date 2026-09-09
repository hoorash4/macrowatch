update public.us_small_business_risk_monthly
set risk_index = survey_risk_index
where survey_risk_index is not null;

comment on table public.us_small_business_risk_monthly is
  'Monthly U.S. small-business risk index: NFIB borrowing difficulty 60 and sales expectations 40; excludes high-yield OAS.';
