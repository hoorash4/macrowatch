update public.economic_chart_catalog_settings
set category_order = (
      select jsonb_agg(
        case when item.category_name = '기업신용' then '기업부실' else item.category_name end
        order by item.ordinality
      )
      from jsonb_array_elements_text(category_order)
        with ordinality as item(category_name, ordinality)
    ),
    updated_at = now()
where category_order ? '기업신용';

update public.economic_chart_preferences
set series_order = (series_order - '기업신용')
                   || jsonb_build_object(
                        '기업부실', coalesce(series_order -> '기업부실', series_order -> '기업신용')
                      ),
    updated_at = now()
where series_order ? '기업신용';
