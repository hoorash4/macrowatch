-- Convert already stored Census MARTS retail-sales levels to YoY percentages.
-- This is a narrowly scoped semantic correction for US_RETAIL_SALES only.
with levels as materialized (
  select observation_date, value
  from public.economic_chart_points
  where series_code = 'US_RETAIL_SALES'
    and source = 'CENSUS:MARTS/44X72/SM/SA'
), yoy as (
  select
    current.observation_date,
    round(((current.value / previous.value) - 1) * 100, 6) as yoy_pct
  from levels current
  join levels previous
    on previous.observation_date = (current.observation_date - interval '1 year')::date
  where previous.value <> 0
)
update public.economic_chart_points points
set value = yoy.yoy_pct,
    source = 'CENSUS:MARTS/44X72/SM/SA/YOY',
    collected_at = now()
from yoy
where points.series_code = 'US_RETAIL_SALES'
  and points.observation_date = yoy.observation_date
  and points.source = 'CENSUS:MARTS/44X72/SM/SA';

-- The first comparison year has no prior-year level in the existing 10-year bootstrap.
-- Remove those nominal-level leftovers so the chart can never mix dollars with percentages.
-- The explicit Census backfill now fetches an extra comparison year and can restore the
-- full ten years as YoY observations when it is run.
delete from public.economic_chart_points
where series_code = 'US_RETAIL_SALES'
  and source = 'CENSUS:MARTS/44X72/SM/SA';
