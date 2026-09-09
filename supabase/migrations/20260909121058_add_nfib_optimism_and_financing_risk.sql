alter table public.us_small_business_risk_monthly
  add column if not exists optimism_index numeric(8, 4),
  add column if not exists financing_risk_index numeric(8, 4);

comment on column public.us_small_business_risk_monthly.optimism_index is
  'NFIB Small Business Optimism Index 원지수';

comment on column public.us_small_business_risk_monthly.financing_risk_index is
  '차입난이도 60%, 하이일드 OAS 40% 위험지수. OAS 이전은 차입난이도 100%';
