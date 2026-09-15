create table if not exists public.historical_cases (
  case_code text primary key,
  display_order smallint not null unique,
  case_name text not null unique,
  primary_index_code text not null
    check (primary_index_code in ('SP500', 'NASDAQ_COMPOSITE', 'KOSPI')),
  comparison_index_codes text[] not null default '{}'::text[],
  search_start date not null,
  search_end date,
  start_date date,
  peak_date date,
  trough_date date,
  cycle_status text not null default 'draft'
    check (cycle_status in ('draft', 'confirmed', 'in_progress')),
  cycle_summary text not null,
  updated_at timestamptz not null default now(),
  updated_by uuid references auth.users(id) on delete set null,
  constraint historical_cases_search_order
    check (search_end is null or search_start <= search_end),
  constraint historical_cases_point_order
    check (
      (start_date is null or start_date >= search_start)
      and (peak_date is null or (start_date is not null and peak_date >= start_date))
      and (trough_date is null or (peak_date is not null and trough_date >= peak_date))
      and (search_end is null or coalesce(trough_date, peak_date, start_date, search_start) <= search_end)
    ),
  constraint historical_cases_status_points
    check (
      (cycle_status = 'confirmed' and start_date is not null and peak_date is not null and trough_date is not null)
      or (cycle_status = 'in_progress' and start_date is not null)
      or cycle_status = 'draft'
    ),
  constraint historical_cases_comparison_indices
    check (comparison_index_codes <@ array['SP500', 'NASDAQ_COMPOSITE', 'KOSPI']::text[])
);

alter table public.historical_cases enable row level security;

create index if not exists historical_cases_updated_by_idx
  on public.historical_cases(updated_by);

drop policy if exists "Authenticated users read historical cases" on public.historical_cases;
drop policy if exists "Administrators insert historical cases" on public.historical_cases;
drop policy if exists "Administrators update historical cases" on public.historical_cases;

create policy "Authenticated users read historical cases"
  on public.historical_cases for select to authenticated using (true);

create policy "Administrators insert historical cases"
  on public.historical_cases for insert to authenticated
  with check (
    exists (
      select 1 from public.user_accounts
      where user_id = (select auth.uid()) and is_admin = true
    )
  );

create policy "Administrators update historical cases"
  on public.historical_cases for update to authenticated
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

revoke all on table public.historical_cases from anon, authenticated;
grant select, insert, update on table public.historical_cases to authenticated;

insert into public.historical_cases (
  case_code, display_order, case_name, primary_index_code, comparison_index_codes,
  search_start, search_end, start_date, peak_date, trough_date, cycle_status, cycle_summary
) values
  ('dotcom', 1, '닷컴버블', 'NASDAQ_COMPOSITE', array['SP500','KOSPI'],
   '1994-01-01', '2003-03-31', '1994-06-24', '2000-03-10', '2002-10-09', 'confirmed',
   '인터넷·기술 투자와 밸류에이션 팽창 뒤 실적 기대 붕괴와 기술주 급락으로 이어진 사이클'),
  ('card_crisis', 2, '카드대란', 'KOSPI', array[]::text[],
   '2001-01-01', '2003-12-31', '2001-09-17', '2002-04-18', '2003-03-17', 'confirmed',
   '카드 발급과 가계신용 확대 뒤 연체율 상승과 카드사 유동성 문제가 금융시장 스트레스로 번진 사이클'),
  ('china_industrials', 3, '중국 산업재 버블', 'KOSPI', array[]::text[],
   '2002-10-01', '2009-03-31', '2003-03-17', '2007-10-31', '2008-10-24', 'confirmed',
   '중국의 고성장과 투자 확대가 산업재 수요와 한국 관련 기업의 실적을 끌어올린 뒤 급락한 사이클'),
  ('global_financial_crisis', 4, '글로벌 금융위기', 'SP500', array[]::text[],
   '2002-01-01', '2009-06-30', '2002-10-09', '2007-10-09', '2009-03-09', 'confirmed',
   '주택·신용과 금융기관 레버리지의 팽창 뒤 신용시장 경색과 금융기관 위기로 이어진 사이클'),
  ('liquidity_2009_2011', 5, '2009~2011 유동성장', 'KOSPI', array[]::text[],
   '2008-09-01', '2012-01-31', '2008-10-24', '2011-05-02', '2011-09-26', 'confirmed',
   '금융위기 저점 이후 정책 대응과 유동성 공급이 위험자산을 끌어올린 뒤 긴축 우려와 유럽 재정위기로 조정된 사이클'),
  ('us_liquidity_2012_2014', 6, '2012~2014 미국 유동성장', 'SP500', array[]::text[],
   '2011-08-01', '2016-06-30', '2011-10-03', '2015-05-21', '2016-02-11', 'confirmed',
   'QE3와 제로금리 아래 금융여건이 완화된 뒤 정책 정상화 우려와 성장 둔화로 조정된 사이클'),
  ('memory_supercycle', 7, '메모리 슈퍼사이클', 'KOSPI', array[]::text[],
   '2016-01-01', '2019-06-30', '2016-02-12', '2018-01-29', '2019-01-03', 'confirmed',
   'DRAM·NAND 수요와 가격 상승 뒤 재고조정·업황 피크아웃과 미중 무역분쟁이 겹친 사이클'),
  ('covid_shock', 8, '코로나 충격', 'SP500', array[]::text[],
   '2018-10-01', '2020-06-30', '2018-12-24', '2020-02-19', '2020-03-23', 'confirmed',
   '코로나 직전 상승 추세가 팬데믹과 유동성 충격으로 급락한 단기 충격 사이클'),
  ('tightening_2022', 9, '2022 금리인상/긴축장', 'NASDAQ_COMPOSITE', array[]::text[],
   '2020-02-01', '2023-03-31', '2020-03-23', '2021-11-19', '2022-12-28', 'confirmed',
   '코로나 이후 유동성 장세 뒤 인플레이션과 금리 상승이 성장주 밸류에이션을 압축한 사이클'),
  ('ai_semiconductor', 10, 'AI/반도체 상승장', 'NASDAQ_COMPOSITE', array[]::text[],
   '2022-06-01', null, '2022-12-28', null, null, 'in_progress',
   '생성형 AI 확산과 GPU·HBM·데이터센터 투자 확대가 진행 중인 미완결 사이클')
on conflict (case_code) do nothing;

comment on table public.historical_cases is
  'Historical Insight case definitions. Dates are confirmed cycle anchors; closes and performance are derived from canonical market_index_prices.';
