alter table public.us_market_stress_lead_weekly
  add column if not exists is_provisional boolean not null default false;

comment on column public.us_market_stress_lead_weekly.is_provisional is
  'True until every available daily and weekly source for the Monday-Sunday week has reported.';
