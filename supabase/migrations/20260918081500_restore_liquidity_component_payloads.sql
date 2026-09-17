alter table public.liquidity_indices
  add column if not exists components jsonb,
  add column if not exists component_scores jsonb;
