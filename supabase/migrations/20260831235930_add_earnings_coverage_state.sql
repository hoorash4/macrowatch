-- Market growth is not meaningful when either side represents less than half
-- of the intended market universe. The row remains auditable, but chartable
-- averages and growth values are withheld by calculation version 4.
alter table public.earnings_market_quarterly_metrics
  drop constraint if exists earnings_market_quarterly_metrics_yoy_state_check;
alter table public.earnings_market_quarterly_metrics
  add constraint earnings_market_quarterly_metrics_yoy_state_check
  check (yoy_state in (
    'normal', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening',
    'loss_unchanged', 'from_zero', 'missing_prior_snapshot',
    'insufficient_coverage'
  ));
