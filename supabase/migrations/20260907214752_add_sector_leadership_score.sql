alter table public.market_sector_weekly_rankings
  add column if not exists leadership_score numeric(5, 2)
  check (leadership_score between 0 and 100);

comment on column public.market_sector_weekly_rankings.leadership_score is
  'Harmonic mean of excess-return strength and persistence on KOSPI up-days over retained 10-week prices; null when the recent 20-session market regime or sample is ineligible.';
