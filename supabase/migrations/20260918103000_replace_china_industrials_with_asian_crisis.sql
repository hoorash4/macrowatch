begin;

delete from public.historical_cases
where case_code = 'china_industrials';

update public.historical_cases
set display_order = display_order + 1000;

insert into public.historical_cases (
  case_code, display_order, case_name, primary_index_code, comparison_index_codes,
  search_start, search_end, cycle_summary, updated_at
) values (
  'asian_financial_crisis', 999, '아시아 외환위기', 'KOSPI',
  array['SP500','NASDAQ_COMPOSITE']::text[],
  date '1992-01-01', date '1999-03-31',
  '아시아 신흥국의 통화·외채 취약성과 자본유출이 한국의 외환·금융위기로 확산된 뒤 급격한 자산가격 조정과 구조조정으로 이어진 사이클',
  now()
)
on conflict (case_code) do update set
  case_name = excluded.case_name,
  primary_index_code = excluded.primary_index_code,
  comparison_index_codes = excluded.comparison_index_codes,
  search_start = excluded.search_start,
  search_end = excluded.search_end,
  cycle_summary = excluded.cycle_summary,
  updated_at = now();

insert into public.historical_case_market_cycles (
  case_code, index_code, start_date, peak_date, trough_date, updated_at
) values
  ('asian_financial_crisis','KOSPI',date '1992-08-21',date '1994-11-08',date '1998-06-16',now()),
  ('asian_financial_crisis','SP500',date '1994-04-04',date '1998-07-17',date '1998-08-31',now()),
  ('asian_financial_crisis','NASDAQ_COMPOSITE',date '1994-06-24',date '1998-07-20',date '1998-10-08',now())
on conflict (case_code, index_code) do update set
  start_date = excluded.start_date,
  peak_date = excluded.peak_date,
  trough_date = excluded.trough_date,
  updated_at = now();

with ordered as (
  select case_code, row_number() over (order by search_start, case_code)::smallint as new_order
  from public.historical_cases
)
update public.historical_cases h
set display_order = ordered.new_order
from ordered
where h.case_code = ordered.case_code;

commit;
