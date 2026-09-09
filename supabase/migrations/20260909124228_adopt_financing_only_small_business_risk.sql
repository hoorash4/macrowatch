update public.us_small_business_risk_monthly
set risk_index = financing_risk_index
where financing_risk_index is not null;

comment on table public.us_small_business_risk_monthly is
  'Monthly U.S. small-business risk index: NFIB borrowing difficulty 60 and HY OAS 40 when available; borrowing difficulty only before OAS coverage.';
