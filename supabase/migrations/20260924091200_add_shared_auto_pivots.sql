begin;
set local statement_timeout = '10s';

-- Expand first so existing readers keep working during the application rollout.
alter table public.historical_indicator_ai_scores
  add column auto_pivots jsonb not null default '[]'::jsonb;

update public.historical_indicator_ai_scores
set auto_pivots = ai_pivots
where auto_pivots is distinct from ai_pivots;

commit;
