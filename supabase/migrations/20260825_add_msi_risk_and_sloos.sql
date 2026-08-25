-- Server-only MSI components.  The browser continues to receive only the
-- derived index, never the component values or weighting formula.
alter table public.us_credit_stress_monthly
  add column if not exists financial_conditions_risk_index numeric(10, 6),
  add column if not exists sloos_tightening_pct numeric(8, 4);

comment on column public.us_credit_stress_monthly.financial_conditions_risk_index is
  'Chicago Fed NFCI Risk Subindex; retained server-side for US-MSI calculation.';

comment on column public.us_credit_stress_monthly.sloos_tightening_pct is
  'SLOOS net percentage tightening C&I standards; quarterly releases are expanded to their three reference months.';
