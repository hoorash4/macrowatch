with expanded as (
  select settings.id,
         jsonb_agg(mapped.category_name order by item.ordinality, mapped.position) as category_order
  from public.economic_chart_catalog_settings as settings
  cross join lateral jsonb_array_elements_text(settings.category_order)
    with ordinality as item(category_name, ordinality)
  cross join lateral (
    select item.category_name as category_name, 1 as position
    where item.category_name <> '금리 · 신용'
    union all
    select '금리', 1 where item.category_name = '금리 · 신용'
    union all
    select '금융신용', 2 where item.category_name = '금리 · 신용'
  ) as mapped
  group by settings.id
)
update public.economic_chart_catalog_settings as settings
set category_order = expanded.category_order,
    updated_at = now()
from expanded
where settings.id = expanded.id
  and settings.category_order ? '금리 · 신용';

with legacy_orders as (
  select prefs.user_id,
         coalesce((
           select jsonb_agg(item.code order by item.ordinality)
           from jsonb_array_elements_text(prefs.series_order -> '금리 · 신용')
             with ordinality as item(code, ordinality)
           where item.code in ('US2Y', 'US10Y', 'US10Y_REAL', 'US10Y2Y',
                               'US_POLICY_RATE_MID', 'KR3Y', 'KR10Y', 'KR10Y3Y')
         ), '[]'::jsonb) as rates,
         coalesce((
           select jsonb_agg(item.code order by item.ordinality)
           from jsonb_array_elements_text(prefs.series_order -> '금리 · 신용')
             with ordinality as item(code, ordinality)
           where item.code in ('HY_OAS', 'NFCI_CREDIT', 'EM_OAS')
         ), '[]'::jsonb) as financial_credit
  from public.economic_chart_preferences as prefs
  where prefs.series_order ? '금리 · 신용'
)
update public.economic_chart_preferences as prefs
set series_order = (prefs.series_order - '금리 · 신용')
                   || jsonb_build_object(
                        '금리', coalesce(prefs.series_order -> '금리', legacy_orders.rates),
                        '금융신용', coalesce(prefs.series_order -> '금융신용', legacy_orders.financial_credit)
                      ),
    updated_at = now()
from legacy_orders
where prefs.user_id = legacy_orders.user_id;
