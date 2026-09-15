-- Historical Insight reads the same canonical daily source as downstream calculations.
-- Only signed-in users can read the three supported market indices.
alter table public.market_index_prices enable row level security;
grant select on table public.market_index_prices to authenticated;
drop policy if exists "Authenticated users read historical indices" on public.market_index_prices;
create policy "Authenticated users read historical indices"
  on public.market_index_prices for select to authenticated
  using (index_code in ('SP500', 'NASDAQ_COMPOSITE', 'KOSPI'));
