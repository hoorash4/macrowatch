alter table public.historical_cases
  add column if not exists pivot_source_index_code text
  check (pivot_source_index_code in ('SP500', 'NASDAQ_COMPOSITE', 'KOSPI'));

update public.historical_cases
set pivot_source_index_code = 'NASDAQ_COMPOSITE'
where case_code = 'tightening_2022'
  and pivot_source_index_code is distinct from 'NASDAQ_COMPOSITE';

