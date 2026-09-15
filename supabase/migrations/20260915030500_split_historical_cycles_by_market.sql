create table if not exists public.historical_case_market_cycles (
  case_code text not null references public.historical_cases(case_code) on delete cascade,
  index_code text not null
    check (index_code in ('SP500', 'NASDAQ_COMPOSITE', 'KOSPI')),
  start_date date,
  peak_date date,
  trough_date date,
  cycle_status text generated always as (
    case
      when start_date is null then 'draft'
      when peak_date is not null and trough_date is not null then 'confirmed'
      else 'in_progress'
    end
  ) stored,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null,
  primary key (case_code, index_code),
  constraint historical_case_market_cycles_point_order
    check (
      (peak_date is null or (start_date is not null and peak_date >= start_date))
      and (trough_date is null or (peak_date is not null and trough_date >= peak_date))
    )
);

create index if not exists historical_case_market_cycles_updated_by_idx
  on public.historical_case_market_cycles(updated_by);

create or replace function public.validate_historical_market_cycle_bounds()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  observation_start date;
  observation_end date;
begin
  select search_start, search_end
    into observation_start, observation_end
  from public.historical_cases
  where case_code = new.case_code;

  if observation_start is null then
    raise exception 'Historical Case % does not exist', new.case_code;
  end if;

  if new.start_date is not null and new.start_date < observation_start then
    raise exception 'START is outside the observation range';
  end if;

  if observation_end is not null
     and coalesce(new.trough_date, new.peak_date, new.start_date, observation_start) > observation_end then
    raise exception 'Cycle point is outside the observation range';
  end if;

  return new;
end;
$$;

drop trigger if exists validate_historical_market_cycle_bounds
  on public.historical_case_market_cycles;
create trigger validate_historical_market_cycle_bounds
  before insert or update on public.historical_case_market_cycles
  for each row execute function public.validate_historical_market_cycle_bounds();

alter table public.historical_case_market_cycles enable row level security;

drop policy if exists "Authenticated users read historical market cycles"
  on public.historical_case_market_cycles;
drop policy if exists "Administrators insert historical market cycles"
  on public.historical_case_market_cycles;
drop policy if exists "Administrators update historical market cycles"
  on public.historical_case_market_cycles;

create policy "Authenticated users read historical market cycles"
  on public.historical_case_market_cycles for select to authenticated using (true);

create policy "Administrators insert historical market cycles"
  on public.historical_case_market_cycles for insert to authenticated
  with check (
    exists (
      select 1 from public.user_accounts
      where user_id = (select auth.uid()) and is_admin = true
    )
  );

create policy "Administrators update historical market cycles"
  on public.historical_case_market_cycles for update to authenticated
  using (
    exists (
      select 1 from public.user_accounts
      where user_id = (select auth.uid()) and is_admin = true
    )
  )
  with check (
    exists (
      select 1 from public.user_accounts
      where user_id = (select auth.uid()) and is_admin = true
    )
  );

revoke all on table public.historical_case_market_cycles from anon, authenticated;
grant select, insert, update on table public.historical_case_market_cycles to authenticated;
revoke all on function public.validate_historical_market_cycle_bounds() from public, anon, authenticated;

insert into public.historical_case_market_cycles (
  case_code, index_code, start_date, peak_date, trough_date
) values
  ('dotcom', 'NASDAQ_COMPOSITE', '1994-06-24', '2000-03-10', '2002-10-09'),
  ('dotcom', 'SP500', '1994-04-04', '2000-03-24', '2002-10-09'),
  ('dotcom', 'KOSPI', '1998-06-16', '2000-01-04', '2001-09-17'),
  ('card_crisis', 'KOSPI', '2001-09-17', '2002-04-18', '2003-03-17'),
  ('card_crisis', 'NASDAQ_COMPOSITE', '2001-09-21', '2002-01-04', '2002-10-09'),
  ('card_crisis', 'SP500', '2001-09-21', '2002-01-04', '2002-10-09'),
  ('china_industrials', 'KOSPI', '2003-03-17', '2007-10-31', '2008-10-24'),
  ('china_industrials', 'NASDAQ_COMPOSITE', '2002-10-09', '2007-10-31', '2009-03-09'),
  ('china_industrials', 'SP500', '2002-10-09', '2007-10-09', '2009-03-09'),
  ('global_financial_crisis', 'SP500', '2002-10-09', '2007-10-09', '2009-03-09'),
  ('global_financial_crisis', 'NASDAQ_COMPOSITE', '2002-10-09', '2007-10-31', '2009-03-09'),
  ('global_financial_crisis', 'KOSPI', '2003-03-17', '2007-10-31', '2008-10-24'),
  ('liquidity_2009_2011', 'KOSPI', '2008-10-24', '2011-05-02', '2011-09-26'),
  ('liquidity_2009_2011', 'NASDAQ_COMPOSITE', '2009-03-09', '2011-04-29', '2011-10-03'),
  ('liquidity_2009_2011', 'SP500', '2009-03-09', '2011-04-29', '2011-10-03'),
  ('us_liquidity_2012_2014', 'SP500', '2011-10-03', '2015-05-21', '2016-02-11'),
  ('us_liquidity_2012_2014', 'NASDAQ_COMPOSITE', '2011-10-03', '2015-07-20', '2016-02-11'),
  ('us_liquidity_2012_2014', 'KOSPI', '2011-09-26', '2015-04-23', '2015-08-24'),
  ('memory_supercycle', 'KOSPI', '2016-02-12', '2018-01-29', '2019-01-03'),
  ('memory_supercycle', 'NASDAQ_COMPOSITE', '2016-02-11', '2018-08-29', '2018-12-24'),
  ('memory_supercycle', 'SP500', '2016-02-11', '2018-09-20', '2018-12-24'),
  ('covid_shock', 'SP500', '2018-12-24', '2020-02-19', '2020-03-23'),
  ('covid_shock', 'NASDAQ_COMPOSITE', '2018-12-24', '2020-02-19', '2020-03-23'),
  ('covid_shock', 'KOSPI', '2019-01-03', '2020-01-22', '2020-03-19'),
  ('tightening_2022', 'NASDAQ_COMPOSITE', '2020-03-23', '2021-11-19', '2022-12-28'),
  ('tightening_2022', 'SP500', '2020-03-23', '2022-01-03', '2022-10-12'),
  ('tightening_2022', 'KOSPI', '2020-03-19', '2021-07-06', '2022-09-30'),
  ('ai_semiconductor', 'NASDAQ_COMPOSITE', '2022-12-28', null, null),
  ('ai_semiconductor', 'SP500', '2022-10-12', null, null),
  ('ai_semiconductor', 'KOSPI', '2022-09-30', null, null)
on conflict (case_code, index_code) do nothing;

alter table public.historical_cases
  drop constraint if exists historical_cases_point_order,
  drop constraint if exists historical_cases_status_points,
  drop column if exists start_date,
  drop column if exists peak_date,
  drop column if exists trough_date,
  drop column if exists cycle_status;

comment on table public.historical_case_market_cycles is
  'Market-specific START, PEAK, and TROUGH dates for each Historical Insight case. Prices and returns are derived from canonical market_index_prices.';
