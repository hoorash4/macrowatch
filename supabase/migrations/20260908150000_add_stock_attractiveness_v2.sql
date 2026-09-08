alter table public.equity_bond_attractiveness_weekly
  add column if not exists equity_return_13w_pct numeric(18, 6);

alter table public.equity_bond_attractiveness_weekly
  alter column relative_return_13w_pct drop not null;
