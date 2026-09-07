alter table public.market_sector_weekly_rankings
  add column if not exists leadership_score numeric(5, 2)
  check (leadership_score between 0 and 100);

comment on column public.market_sector_weekly_rankings.leadership_score is
  'Relative sector leadership on KOSPI up-days over the latest 20 trading sessions; null when the market regime or sample is ineligible.';
