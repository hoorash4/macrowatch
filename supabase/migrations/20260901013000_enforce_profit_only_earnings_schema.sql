-- Prevent retired revenue aggregates from being written back by old clients.
delete from public.earnings_market_quarterly_metrics
where metric = 'revenue';

alter table public.earnings_market_quarterly_metrics
  drop constraint if exists earnings_market_quarterly_metrics_metric_check;

alter table public.earnings_market_quarterly_metrics
  add constraint earnings_market_quarterly_metrics_metric_check
  check (metric in ('operating_income', 'net_income'));
