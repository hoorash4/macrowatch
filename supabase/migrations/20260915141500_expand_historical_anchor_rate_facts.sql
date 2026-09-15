create or replace view public.historical_case_anchor_facts
with (security_invoker = true)
as
with anchors as (
  select
    cycles.case_code,
    cycles.index_code,
    anchor.anchor_type,
    anchor.anchor_order,
    anchor.anchor_date
  from public.historical_case_market_cycles as cycles
  cross join lateral (
    values
      ('start'::text, 1::smallint, cycles.start_date),
      ('peak'::text, 2::smallint, cycles.peak_date),
      ('trough'::text, 3::smallint, cycles.trough_date)
  ) as anchor(anchor_type, anchor_order, anchor_date)
  where anchor.anchor_date is not null
),
fact_definitions as (
  select *
  from (values
    ('ALL'::text, 'US_POLICY_RATE_MID'::text, '정책금리'::text, '통화정책'::text, '%'::text, 3::smallint, 1::smallint, 0::smallint, 130::smallint),
    ('ALL', 'US2Y', '미국 2년물', '시장금리', '%', 2, 2, 0, 10),
    ('ALL', 'US10Y', '미국 10년물', '시장금리', '%', 2, 3, 0, 10),
    ('ALL', 'US10Y2Y', '미국 10Y-2Y', '금리곡선', '%p', 2, 4, 0, 10),
    ('ALL', 'US_CPI', '미국 CPI', '물가', '% YoY', 2, 5, 45, 120),
    ('ALL', 'NFCI_CREDIT', '미국 신용여건', '금융여건', '지수', 3, 6, 7, 21),
    ('ALL', 'USDKRW', '원/달러', '환율', '원', 2, 7, 0, 10),
    ('KOSPI', 'KR_CORP_CREDIT_SPREAD', '한국 회사채 스프레드', '한국 신용', '%p', 3, 8, 0, 10),
    ('KOSPI', 'KOSPI_PER', 'KOSPI PER', '밸류에이션', '배', 2, 9, 0, 10)
  ) as definitions(
    market_scope, series_code, fact_label, category, unit,
    decimals, display_order, release_lag_days, max_age_days
  )
)
select
  anchors.case_code,
  anchors.index_code,
  anchors.anchor_type,
  anchors.anchor_order,
  anchors.anchor_date,
  facts.series_code,
  facts.fact_label,
  facts.category,
  facts.unit,
  facts.decimals,
  facts.display_order,
  point.observation_date,
  point.available_date,
  point.value,
  case when point.observation_date is null then null else anchors.anchor_date - point.observation_date end as observation_age_days
from anchors
join fact_definitions as facts
  on facts.market_scope = 'ALL' or facts.market_scope = anchors.index_code
left join lateral (
  select
    series.observation_date,
    series.observation_date + facts.release_lag_days::integer as available_date,
    series.value
  from public.economic_chart_series_points as series
  where series.series_code = facts.series_code
    and series.observation_date + facts.release_lag_days::integer <= anchors.anchor_date
    and series.observation_date >= anchors.anchor_date - facts.max_age_days::integer
  order by series.observation_date desc
  limit 1
) as point on true;

comment on view public.historical_case_anchor_facts is
  'Deterministic latest canonical facts available on or before each market-specific START, PEAK, and TROUGH anchor.';

revoke all on table public.historical_case_anchor_facts from public, anon, authenticated;
grant select on table public.historical_case_anchor_facts to authenticated, service_role;
