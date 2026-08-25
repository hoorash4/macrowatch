alter table public.em_market_stress_weekly
  add column if not exists vxeem_4w_average numeric(12, 4);

comment on column public.em_market_stress_weekly.vxeem_4w_average is
  'VXEEM trailing four-week average for the published auxiliary signal.';
