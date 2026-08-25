alter table public.us_credit_stress_monthly
  add column if not exists excess_bond_premium numeric(10, 6),
  add column if not exists corporate_bond_market_distress_index numeric(10, 6);

comment on column public.us_credit_stress_monthly.excess_bond_premium is
  'Federal Reserve Board monthly Excess Bond Premium (EBP).';

comment on column public.us_credit_stress_monthly.corporate_bond_market_distress_index is
  'New York Fed monthly Market Corporate Bond Market Distress Index (CMDI).';
