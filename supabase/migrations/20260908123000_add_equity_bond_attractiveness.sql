create table if not exists public.equity_bond_attractiveness_weekly (
  country text not null check (country in ('KR', 'US')),
  observation_date date not null,
  score numeric(12, 6) not null check (score between -100 and 100),
  earnings_yield_pct numeric(18, 6) not null,
  sovereign_yield_pct numeric(18, 6) not null,
  yield_gap_pct numeric(18, 6) not null,
  earnings_momentum_pct numeric(18, 6) not null,
  relative_return_13w_pct numeric(18, 6) not null,
  component_scores jsonb not null,
  method_version text not null,
  calculated_at timestamptz not null default now(),
  primary key (country, observation_date, method_version)
);

alter table public.equity_bond_attractiveness_weekly enable row level security;
revoke all on public.equity_bond_attractiveness_weekly from public, anon;
grant select on public.equity_bond_attractiveness_weekly to authenticated;
grant select, insert, update, delete on public.equity_bond_attractiveness_weekly to service_role;

drop policy if exists equity_bond_attractiveness_authenticated_read on public.equity_bond_attractiveness_weekly;
create policy equity_bond_attractiveness_authenticated_read
  on public.equity_bond_attractiveness_weekly for select to authenticated using (true);

create or replace function public.equity_bond_attractiveness_inputs()
returns table (
  country text,
  reference_date date,
  net_income_total numeric,
  market_cap_total numeric
)
language sql stable security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select
    case q.market_id when 'kr_largecap' then 'KR' else 'US' end,
    q.reference_date,
    q.net_income_total,
    case when q.market_id = 'kr_largecap' then sum(u.market_cap) else null end
  from earnings_v2.market_quarters q
  join earnings_v2.universe_members u
    on u.market_id = q.market_id
   and u.market_year = q.market_year
   and u.market_quarter = q.market_quarter
  where q.market_id in ('kr_largecap', 'us_sp100')
    and q.market_year >= 2016
    and q.net_income_total is not null
  group by q.market_id, q.market_year, q.market_quarter, q.reference_date, q.net_income_total
  order by q.reference_date;
$$;

revoke all on function public.equity_bond_attractiveness_inputs() from public, anon, authenticated;
grant execute on function public.equity_bond_attractiveness_inputs() to service_role;

-- The project's DDL event trigger applies its generic table grants after policy
-- creation. Finish with the narrower contract required by this read-only chart.
revoke insert, update, delete, truncate, references, trigger
  on public.equity_bond_attractiveness_weekly from authenticated;
grant select on public.equity_bond_attractiveness_weekly to authenticated;
